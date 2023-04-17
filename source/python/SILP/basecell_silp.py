"""
Dataset Generation
Topology Optimization of the Base Cell of a Periodic Metamaterial
--------------------------------------------------------------------
Laboratory of Topology Optimization and Multiphysics Analysis
Department of Computational Mechanics
School of Mechanical Engineering
University of Campinas (Brazil)
--------------------------------------------------------------------
author  : Daniel Candeloro Cunha
version : 1.0
date    : May 2023
--------------------------------------------------------------------
To collaborate or report bugs, please look for the author's email
address at https://www.fem.unicamp.br/~ltm/

All codes and documentation are publicly available in the following
github repository: https://github.com/Joquempo/Metamaterial-Dataset

If you use this program (or the data generated by it) in your work,
the developer would be grateful if you would cite the indicated
references. They are listed in the "CITEAS" file available in the
github repository.
--------------------------------------------------------------------
Copyright (C) 2023 Daniel Candeloro Cunha

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see https://www.gnu.org/licenses
"""

#%% Imports
import os, sys, gc
import numpy as np
from time import perf_counter
from datetime import datetime
from scipy.sparse import coo_matrix
from sksparse.cholmod import analyze

from mesh import get_mesh, get_fmesh
from elem import get_emat, get_augmat
from filters import get_sfil, get_mope
from rem_islands import visit, get_neighbors
from topopt import update, ws
from ilp_solver import solve_ILP, solve_BESO

sys.path.append('../../cython/')
from silp_sens import cgs

#%% Setup

# fixed properties
Ns = 32           # number of elements in each side of the design domain
Eyvar = 0.05      # maximal decrease in Young's modulus per iteration
nuvar = 2.0       # maximal variation in Poisson's ratio per iteration
Dmax  = 0.015625  # maximal topology variation
rsen  = 0.024     # sensitivity filter radius
rmor  = 0.018     # morphology filter radius
patience = 30     # patience stop criterion
momentum = 0.25   # sensitivity momentum
beta = 0.05       # volume penalization factor
Ey = 1.00         # Young's modulus of the base material
nu = 0.30         # Poisson's ratio of the base material
pk = 1e-9         # soft-kill parameter
small = 1e-14     # small value to compare float numbers

noptf   = 7       # number of optimizations to be stored in the same file
fid_ini = 0       # initial input index |run from input 0
fid_lim = 18382   # input index limit   |up to input 18381

# area = 6*Lx*Ly = 1.0
Lx = 1.0/(108**0.25)  # design domain shorter side
Ly = np.sqrt(3)*Lx    # design domain longer side
Lex = Lx/Ns           # element shorter side
Ley = np.sqrt(3)*Lex  # element longer side

N = Ns**2                   # number of elements in the design domain
Nt = 6*N                    # number of elements in the base cell
M = 1 + 6*Ns*(Ns+1)         # number of nodes in the base cell
G = 2*M                     # number of degrees of freedom in the base cell
dXmax = int(round(N*Dmax))  # maximal topology variation (number of elements)

# Generate Mesh
### numbering rule
###          ____ ____ ____ ____
###         32   31   30   29  28 
###        /_(20)|(19)|(18)|(17)_\
###       33 \_  |    |    |  _/ 27
###      /_(21)\_|____|____|_/(16)_\
###     34 \_    10   9    8    _/ 26
###    / (22)\_ / (4) | (3) \ _/(15) \
###   35\__    11\__  |  __/ 7    __/25
###  /     \_ /     \_|_/     \ _/     \
### 36 (23) _12 (5)  _0_  (2)  6_ (14) 24
###  \   __/  \   __/ | \__   /  \__   /
###   13/ (6) _1_/    |    \_5_ (13)\23
###    \    _/  \ (0) | (1) /  \_    /
###     14_/ (7)_2____3____4_(12)\_22
###      \    _/ |    |    | \_    /
###       15_/   |    |    |   \_21
###        \ (8) |(9) |(10)|(11) /
###        16___17___18___19___20
coor, inci, etype, sym = get_mesh(Ns, Lex, Ley)

# Element Matrices (Quad4) - Plane Stress State
### numbering rule
###     __/3|     |3\__          __/ 2       2_____1       1_____0       0 \__
###  __/    |     |    \__     _/     \      |      \     /      |      /     \_
### 0   (0) |     | (1)   2   3_  (2)  1     | (3) __0   2__ (4) |     1  (5)  _3
###  \      |     |      /      \__   /      |  __/         \__  |      \   __/ 
###   1_____2     0_____1          \_0       |3/               \3|       2_/
###
###     _/ 3       3____2        2 \_          _/ 1        1____0       0 \_
###   _/    \      |    |       /    \_      _/    \       |    |      /    \_
###  0  (6) _2     |(7) |      3_ (8)  1    2  (9) _0      |(10)|     1_ (11) 3
###   \   _/       |    |        \_   /      \   _/        |    |       \_   /
###    1_/         0____1          \_0        3_/          2____3         \_2
Ket = get_emat(Ey,nu)
Ketvec = np.ndarray((12,64))
dKe = np.ndarray((12,8,8))
for ek in range(12):
    Ketvec[ek,:] = Ket[ek,:,:].ravel()
    dKe[ek,:,:] = (1.0-pk)*Ket[ek,:,:]  # stiffness variation of a topological change
# augmented element matrices (6xQuad4)
aug_etype, Hlist, dKelist = get_augmat(Ns,inci,etype,sym,dKe)

# Initial Topology
x_init = np.ones(N,dtype=bool)    # design variables
Ntotal = Ns//16
Nhole = Ntotal
while Nhole > 0:
    x_init[(Ns-1-(Ntotal-Nhole))*Ns+(Ns//2-Nhole):(Ns-1-(Ntotal-Nhole))*Ns+(Ns//2+Nhole)] = False
    Nhole = Nhole - 1
xt = np.ndarray((Nt),dtype=bool)  # symmetric density vector
for k in range(N):
    xt[sym[k,:]] = x_init[k]

# Generate Extended Mesh
if (not os.path.exists('./sfil_data.npy')) or (not os.path.exists('./mfil_data.npy')) or (not os.path.exists('./neighbors.npy')):
    ### numbering rule
    ###                     ____ ____ ____ ____
    ###                    32   31   30   29  28 
    ###                   /_(20)|(19)|(18)|(17)_\
    ###                  33 \_  |    |    |  _/ 27
    ###                 /_(21)\_|____|____|_/(16)_\
    ###                34 \_    10   9    8    _/ 26
    ###               / (22)\_ / (4) | (3) \ _/(15) \
    ###              35\__    11\__  |  __/ 7    __/25
    ###   ____ ____ /     \_ /     \_|_/     \ _/     \
    ###  46   45   36 (23) _12 (5)  _0_  (2)  6_ (14) 24
    ###  |(31)|(30) \   __/  \   __/ | \__   /  \__   /
    ###  |    |   __/13/ (6) _1_/    |    \_5_ (13)\23
    ###  |____| _/(29)\    _/  \ (0) | (1) /  \_    /
    ###  42   41    __/14_/ (7)_2____3____4_(12)\_22
    ###  |(25) \  _/(28)\    _/ |    |    | \_    /
    ###  |   __/40    __/15_/   |    |    |   \_21
    ###  | _/    \  _/    \ (8) |(9) |(10)|(11) /
    ###  37_ (24) 39_ (27) 16___17___18___19___20
    ###     \__  /   \__  /     |    |    |     \
    ###        \38_ (26)\44_(38)|(37)|(36)|(35)_54
    ###            \__   /  \_  |    |    |  _/  \
    ###               \_/_(39)\_|____|____|_/(34)_\
    ###                43 \_    51   50   49   _/ 53
    ###                     \_ / (33)|(32) \ _/
    ###                       52\__  |  __/48   
    ###                            \_|_/     
    ###                              47 
    coor_lb, coor_bot, inci_lb, inci_bot, sym_lb, sym_bot = get_fmesh(Ns, Lx, Ly, Lex, Ley)

# Sensitivity and Morphology Filters Matrices
if os.path.exists('./sfil_data.npy') and os.path.exists('./mfil_data.npy'):
    data = np.load('./sfil_data.npy')
    row  = np.load('./sfil_row.npy')
    col  = np.load('./sfil_col.npy')
    Sf = coo_matrix((data,(row,col)),shape=(N,N))
    Sf = Sf.tocsr()
    data = np.load('./mfil_data.npy')
    row  = np.load('./mfil_row.npy')
    col  = np.load('./mfil_col.npy')
    Mf = coo_matrix((data,(row,col)),shape=(N,N))
    Mf = Mf.tocsr()              
else:
    # extended mesh
    fcoor = np.vstack((coor,coor_lb,coor_bot))
    finci = np.vstack((inci,inci_lb,inci_bot))
    fsym = np.hstack((sym,sym_lb,sym_bot))
    elepos = 0.25*fcoor[finci].sum(axis=1)
    row = fsym.ravel('C')
    col = np.repeat(np.arange(N),10)
    data = np.ones(10*N)
    Q = coo_matrix((data,(row,col)),shape=(10*N,N))
    Q = Q.tocsc()
    # sensitivity filter matrix
    Sf = get_sfil(N,sym,elepos,Q,rsen)
    Sfcoo = Sf.tocoo()
    np.save('./sfil_data.npy',Sfcoo.data)
    np.save('./sfil_row.npy',Sfcoo.row)
    np.save('./sfil_col.npy',Sfcoo.col)
    # morphology filter matrix
    Mf = get_mope(N,sym,elepos,Q,rmor)
    Mfcoo = Mf.tocoo()
    np.save('./mfil_data.npy',Mfcoo.data)
    np.save('./mfil_row.npy',Mfcoo.row)
    np.save('./mfil_col.npy',Mfcoo.col)

# Get Neighbors
if os.path.exists('./neighbors.npy'):
    neighbors = np.load('./neighbors.npy')
else:
    neighbors = get_neighbors(Ns,inci,inci_lb,inci_bot,sym,sym_lb,sym_bot)
    np.save('./neighbors.npy',neighbors)

# Periodic Boundary Conditions
# constraint matrix
Gb = 4*Ns
Gd = G - 2 - 6*Gb
Gr = Gd + 3*Gb - 2
ivec = np.arange(2,G)
j0 = np.arange(0,Gd)
j1 = np.arange(Gd,Gd+3*Gb-2)
v1=np.arange(Gd+Gb-2,Gd-1,-2)
v2=np.arange(Gd+Gb-1,Gd,-2)
j2=np.vstack((v1,v2)).ravel('F')
v1=np.arange(Gd+2*Gb-2,Gd+Gb-3,-2)
v2=np.arange(Gd+2*Gb-1,Gd+Gb-2,-2)
j3=np.vstack((v1,v2)).ravel('F')
v1=np.arange(Gd+3*Gb-4,Gd+2*Gb-3,-2)
v2=np.arange(Gd+3*Gb-3,Gd+2*Gb-2,-2)
j4=np.vstack((v1,v2)).ravel('F')
jvec = np.concatenate((j0,j1,j2,j3,j4))
avec = np.ones(G-2)
P = coo_matrix((avec,(ivec,jvec)),shape=(G,Gr)).tocsr()
# macro-strain tensors
eps_xx = np.array([[1,0],[0,0]])
eps_yy = np.array([[0,0],[0,1]])
eps_xy = np.array([[0,0.5],[0.5,0]])
# macro-displacements vectors
uhat_xx = np.ravel(coor @ eps_xx, 'C')
uhat_yy = np.ravel(coor @ eps_yy, 'C')
uhat_xy = np.ravel(coor @ eps_xy, 'C')
Uhat = np.vstack((uhat_xx,uhat_yy,uhat_xy)).T

# check directories
if not os.path.exists('../input'):
    os.mkdir('../input')
if not os.path.exists('./output'):
    os.mkdir('./output')
if not os.path.exists('./output/run_{:05d}_{:05d}'.format(fid_ini,fid_lim-1)):
    os.mkdir('./output/run_{:05d}_{:05d}'.format(fid_ini,fid_lim-1))

# check input
if not os.path.exists('../input/inputmat.npy'):
    nuval = np.float32(0.00)  # target Poisson's ratio
    Eymin = np.float32(0.10)  # minimal Young's modulus
    inputmat = np.array([[nuval,Eymin]])
    np.save('../input/inputmat.npy',inputmat)
# read input file
inputmat = np.load('../input/inputmat.npy')

# open log files
if not os.path.exists('./output/run_{:05d}_{:05d}/logs'.format(fid_ini,fid_lim-1)):
    os.mkdir('./output/run_{:05d}_{:05d}/logs'.format(fid_ini,fid_lim-1))
iolog = open('./output/run_{:05d}_{:05d}/logs/io_log.txt'.format(fid_ini,fid_lim-1),'a')
tlog = open('./output/run_{:05d}_{:05d}/logs/time_log.txt'.format(fid_ini,fid_lim-1),'a')
iolog.truncate(0)
tlog.truncate(0)

# write headers
iolog.write('BASE CELL OPTIMIZATION (IO LOG)\n')    # write in IO log
iolog.write('======================================================================================\n')
iolog.write('= OUTPUT :                  input file id :              fid.npy                     =\n')
iolog.write('= ------ :                     input data :              inp.npy                     =\n')
iolog.write('= ------ :             optimized topology :          top_opt.npy                     =\n')
iolog.write('= ------ :      optimized Poisson\'s ratio :           nu_opt.npy                     =\n')
iolog.write('= ------ :      optimized Young\'s modulus :           Ey_opt.npy                     =\n')
iolog.write('= ------ :   pointer input > optimization :          ptr2opt.npy                     =\n')
iolog.write('= ------ :   pointer optimization > input :          ptr2inp.npy                     =\n')
iolog.write('= ------ :               topology vectors :              top.npy                     =\n')
iolog.write('= ------ :       xx-displacements vectors :           dis_xx.npy                     =\n')
iolog.write('= ------ :       yy-displacements vectors :           dis_yy.npy                     =\n')
iolog.write('= ------ :       xy-displacements vectors :           dis_xy.npy                     =\n')
iolog.write('= ------ : dC00_CGS-0 sensitivity vectors :           dC00_0.npy                     =\n')
iolog.write('= ------ : dC00_CGS-1 sensitivity vectors :           dC00_1.npy                     =\n')
iolog.write('= ------ : dC00_CGS-2 sensitivity vectors :           dC00_2.npy                     =\n')
iolog.write('= ------ :    dC00_WS sensitivity vectors :           dC00_w.npy                     =\n')
iolog.write('= ------ : dC11_CGS-0 sensitivity vectors :           dC11_0.npy                     =\n')
iolog.write('= ------ : dC11_CGS-1 sensitivity vectors :           dC11_1.npy                     =\n')
iolog.write('= ------ : dC11_CGS-2 sensitivity vectors :           dC11_2.npy                     =\n')
iolog.write('= ------ :    dC11_WS sensitivity vectors :           dC11_w.npy                     =\n')
iolog.write('= ------ : dC22_CGS-0 sensitivity vectors :           dC22_0.npy                     =\n')
iolog.write('= ------ : dC22_CGS-1 sensitivity vectors :           dC22_1.npy                     =\n')
iolog.write('= ------ : dC22_CGS-2 sensitivity vectors :           dC22_2.npy                     =\n')
iolog.write('= ------ :    dC22_WS sensitivity vectors :           dC22_w.npy                     =\n')
iolog.write('= ------ :          Poisson\'s ratio array :               nu.npy                     =\n')
iolog.write('= ------ :          Young\'s modulus array :               Ey.npy                     =\n')
iolog.write('= ------ :                   volume array :              vol.npy                     =\n')
iolog.write('= ------ :                     time array :              tim.npy                     =\n')
iolog.write('======================================================================================\n')
iolog.write('      INPUT || NUVAL : EYMIN >> NUOPT : EYOPT ||             BEGIN :               END\n')
tlog.write('BASE CELL OPTIMIZATION (TIME LOG)\n')   # write in time log
tlog.write('===========================================================================================\n') 
tlog.write('      INPUT || (  IT x ):    M-ILP : M-SOLVER :    M-CGS :     M-WS :   M-POST ||     TOTAL\n')

#%% Initial Analysis

# Assembly
# COO data
pen = np.ones(Nt)
pen[~xt] = pk
data = np.ndarray((64*Nt))
for et in range(Nt):
    ek = etype[et]
    data[64*et:64*et+64] = pen[et]*Ketvec[ek,:]
# COO indices
dof0 = 2*inci[:,0]
dof1 = dof0 + 1
dof2 = 2*inci[:,1]
dof3 = dof2 + 1
dof4 = 2*inci[:,2]
dof5 = dof4 + 1
dof6 = 2*inci[:,3]
dof7 = dof6 + 1
eledofs = np.array([dof0,dof1,dof2,dof3,dof4,dof5,dof6,dof7])
row = eledofs.repeat(8,axis=0).ravel('F')
col = eledofs.T.repeat(8,axis=0).ravel('C')
# stiffness matrix
Kg_coo_init = coo_matrix((data,(row,col)),shape=(G,G))
Kg_csc = Kg_coo_init.tocsc()
Kr = P.T @ Kg_csc @ P
# maneuver to fix the pattern of non-zero entries
Z_coo = coo_matrix((np.ones(64*Nt),(row,col)),shape=(G,G))
Z_csc = Z_coo.tocsc()
Zr = P.T @ Z_csc @ P
Zr.sort_indices()
shift = 10*np.amax(abs(Ket))
Kr = Kr + shift*Zr
Kr.sort_indices()
Kr.data = Kr.data - shift*Zr.data
# right-hand side
Fr = -P.T @ Kg_csc @ Uhat

# Solve System
# analyze sparse matrix
factor = analyze(Kr)
# call solver
factor.cholesky_inplace(Kr)
Ur = factor(Fr)
Ug_init = Uhat + P @ Ur

# Effective Properties Matrix
Ch_init = Ug_init.T @ Kg_csc @ Ug_init
gamma_init = Ch_init[2,2]/Ch_init[0,0]
nuhat_init = 1-2*Ch_init[2,2]/Ch_init[0,0]
Eyhat_init = 4*Ch_init[2,2]*(Ch_init[0,0]-Ch_init[2,2])/Ch_init[0,0]
vol_init = sum(x_init)/N

# initialize sensitivity arrays
s_Ey = np.ndarray((N))
s_obj = np.ndarray((N))
dC00_0_init = np.ndarray((N))
dC11_0_init = np.ndarray((N))
dC22_0_init = np.ndarray((N))
dC00_1_init = np.ndarray((N))
dC11_1_init = np.ndarray((N))
dC22_1_init = np.ndarray((N))
dC00_2_init = np.ndarray((N))
dC11_2_init = np.ndarray((N))
dC22_2_init = np.ndarray((N))
cgs(dC00_0_init,dC11_0_init,dC22_0_init,dC00_1_init,dC11_1_init,dC22_1_init,
    dC00_2_init,dC11_2_init,dC22_2_init,x_init,N,sym,etype,aug_etype,inci,Ug_init,dKe,P,Kr,dKelist)
dC00_w_init, dC11_w_init, dC22_w_init = ws(x_init,aug_etype,sym,P,factor,inci,Ug_init,Hlist)

#%% Setup Optimization

file = 0  # file counter
fid = max([0,fid_ini])
while fid < min([fid_lim,inputmat.shape[0]]):
    if not os.path.exists('./output/run_{:05d}_{:05d}/file_{:04d}'.format(fid_ini,fid_lim-1,file)):
        os.mkdir('./output/run_{:05d}_{:05d}/file_{:04d}'.format(fid_ini,fid_lim-1,file)) 

    # initialize lists
    list_fid     = []
    list_inp     = []
    list_top_opt = []
    list_nu_opt  = []
    list_Ey_opt  = []
    list_ptr2opt = []
    list_ptr2inp = []
    list_top     = []
    list_dis_xx  = []
    list_dis_yy  = []
    list_dis_xy  = []
    list_dC00_0  = []
    list_dC00_1  = []
    list_dC00_2  = []
    list_dC00_w  = []
    list_dC11_0  = []
    list_dC11_1  = []
    list_dC11_2  = []
    list_dC11_w  = []
    list_dC22_0  = []
    list_dC22_1  = []
    list_dC22_2  = []
    list_dC22_w  = []
    list_nu      = []
    list_Ey      = []
    list_vol     = []
    list_tim     = []
    
    ptr = 0  # pointer to input
    for counter in range(noptf):
        if fid >= min([fid_lim,inputmat.shape[0]]):
            break

        print('running : {:05d} : setup'.format(fid))
        inp_id = 'inp_{:05d}'.format(fid)
        iolog.write('> ' + inp_id + ' ||')
        tlog.write('> ' + inp_id + ' ||')
        
        nuval = inputmat[fid,0]  # target Poisson's ratio
        Eymin = inputmat[fid,1]  # minimal Young's modulus
        list_fid += [fid]
        list_inp += [[nuval,Eymin]]
        
        # write in log
        iolog.write(' {:5.2f} :'.format(nuval))
        iolog.write(' {:5.3f} >>'.format(Eymin))
        begin = datetime.now().strftime(' %y/%m/%d-%H:%M:%S :')
        
        # get initial data
        x      = x_init.copy()
        Kg_coo = Kg_coo_init.copy()
        Ug     = Ug_init.copy()
        Ch     = Ch_init.copy()
        gamma  = gamma_init
        nuhat  = nuhat_init
        Eyhat  = Eyhat_init
        vol    = vol_init
        dC00_0 = dC00_0_init.copy()
        dC00_1 = dC00_1_init.copy()
        dC00_2 = dC00_2_init.copy()
        dC00_w = dC00_w_init.copy()
        dC11_0 = dC11_0_init.copy()
        dC11_1 = dC11_1_init.copy()
        dC11_2 = dC11_2_init.copy()
        dC11_w = dC11_w_init.copy()
        dC22_0 = dC22_0_init.copy()
        dC22_1 = dC22_1_init.copy()
        dC22_2 = dC22_2_init.copy()
        dC22_w = dC22_w_init.copy()
        
        # target Poisson's ratio (limited variation)
        if nuhat > nuval:
            eta_nu = max([nuhat - nuval - nuvar, 0.0])
        else:
            eta_nu = min([nuhat - nuval + nuvar, 0.0])
        nu_g = nuval + eta_nu
        fnu_g = (nuhat-nu_g)**2
        
        # minimal Young's modulus (limited variation)
        fEy = Eyhat-Eymin
        eta_Ey = max([fEy - Eyvar, 0.0])
        
        # sensitivity analysis
        for e in range(N):
            ggamma = (Ch[2,2]+dC22_w[e])/(Ch[0,0]+dC00_w[e])
            dnu = 2.0*(gamma*dC00_w[e]-dC22_w[e])/(Ch[0,0]+dC00_w[e])
            dEy = 4.0*(1.0-ggamma)*dC22_w[e] + 2.0*Ch[2,2]*dnu
            dobj = 2.0*(nuhat-nu_g)*dnu + dnu*dnu
            if x[e]:
                s_Ey[e]  = -dEy
                s_obj[e] = -dobj
            else:
                s_Ey[e]  =  dEy
                s_obj[e] =  dobj
        raw_obj = s_obj + beta/N
        obj = fnu_g + beta*vol
        fil_Ey  = Sf @ s_Ey
        fil_obj = Sf @ raw_obj
        mom_obj = np.zeros(N)
        mom_obj = momentum*mom_obj + (1.0-momentum)*fil_obj/max(abs(fil_obj))
        mom_obj = mom_obj/max(abs(mom_obj))
        
        # store data
        size_list = len(list_ptr2inp)
        list_ptr2opt += [size_list]
        list_ptr2inp += [ptr]
        list_top     += [x.copy()]
        list_dis_xx  += [Ug[:,0].copy()]
        list_dis_yy  += [Ug[:,1].copy()]
        list_dis_xy  += [Ug[:,2].copy()]
        list_dC00_0  += [dC00_0.copy()]
        list_dC00_1  += [dC00_1.copy()]
        list_dC00_2  += [dC00_2.copy()]
        list_dC00_w  += [dC00_w.copy()]
        list_dC11_0  += [dC11_0.copy()]
        list_dC11_1  += [dC11_1.copy()]
        list_dC11_2  += [dC11_2.copy()]
        list_dC11_w  += [dC11_w.copy()]
        list_dC22_0  += [dC22_0.copy()]
        list_dC22_1  += [dC22_1.copy()]
        list_dC22_2  += [dC22_2.copy()]
        list_dC22_w  += [dC22_w.copy()]
        list_nu      += [nuhat]
        list_Ey      += [Eyhat]
        list_vol     += [vol]
        
        # optimized topology thus far
        top_opt = x.copy()
        nu_opt = nuhat
        Ey_opt = Eyhat
        obj_opt = obj
        
        time_array = np.zeros(7)  # initialize time array   
        keep_going = True
        waiting = 0
        it = 0
        
        #%% Optimization (SILP)
        while keep_going:
            it = it + 1
            print('running : {:05d} : {:5d}'.format(fid,it))
            
            # solve ILP
            t0 = perf_counter()
            selection = (x & (mom_obj>-small)) | ((~x) & (mom_obj<small)) | (x & (fil_Ey<small)) | ((~x) & (fil_Ey>-small))
            Nsel = sum(selection)
            y = x.copy()
            if Nsel > 0:
                if (Eymin + eta_Ey) < small:
                    ysel = solve_BESO(Nsel,x[selection],mom_obj[selection],dXmax)
                    y[selection] = ysel
                else:
                    ysel = solve_ILP(Nsel,x[selection],mom_obj[selection],fil_Ey[selection],fEy,eta_Ey,dXmax,sense_h='G')
                    y[selection] = ysel
            # open operator (erode + dilate)
            y[Mf[~y,:].indices] = False
            y[Mf[y,:].indices] = True
            # remove islands
            voly = sum(y)/N
            continent = np.zeros(N,dtype=bool)
            for e in (list(range(0,Ns))+list(range(Ns,N,Ns))):
                if y[e] and (not continent[e]):
                    continent = np.zeros(N,dtype=bool)    
                    visit(e,y,continent,neighbors)
                    continent_vol = sum(continent)/N
                if continent_vol > 0.50*voly:
                    break
            if continent_vol > 0.50*voly:
                islands = np.argwhere(y!=continent).ravel()
                if len(islands) > dXmax:
                    sortedargs = np.argsort(abs(fil_Ey[islands]))
                    y[islands[sortedargs[:dXmax]]] = False
                else:
                    y[islands] = False
            # erode if nothing has been changed
            if all(x==y):
                print('--- erode ---')
                y[Mf[~y,:].indices] = False
            t1 = perf_counter()
            time_array[0] += (t1-t0)
            
            # update topology
            t0 = perf_counter()
            if any(x!=y):
                elist = list(np.argwhere(x!=y)[:,0])
                Ug, Kr = update(x,etype,sym,pk,Ketvec,P,Kg_coo,Zr,shift,Uhat,factor,elist)
            # compute homogenized properties
            Kg_csc = Kg_coo.tocsc()
            Ch = Ug.T @ Kg_csc @ Ug
            gamma = Ch[2,2]/Ch[0,0]
            nuhat = 1-2*Ch[2,2]/Ch[0,0]
            Eyhat = 4*Ch[2,2]*(Ch[0,0]-Ch[2,2])/Ch[0,0]
            if nuhat > nuval:
                eta_nu = max([nuhat - nuval - nuvar, 0.0])
            else:
                eta_nu = min([nuhat - nuval + nuvar, 0.0])
            nu_g = nuval + eta_nu
            fnu_g = (nuhat-nu_g)**2
            vol = sum(x)/N
            obj = fnu_g + beta*vol
            fEy_test = Eyhat-Eymin
            eta_Ey_test = max([fEy_test - Eyvar, 0.0])
            # go back to last topology and dilate if constraint is broken
            if fEy_test < 0.0:
                # go back to last topology
                update(x,etype,sym,pk,Ketvec,P,Kg_coo,Zr,shift,Uhat,factor,elist,solve_sys=False)
                # dilate
                print('--- dilate ---')
                y = x.copy()
                y[Mf[y,:].indices] = True
                elist = list(np.argwhere(x!=y)[:,0])
                # compute homogenized properties
                Ug, Kr = update(x,etype,sym,pk,Ketvec,P,Kg_coo,Zr,shift,Uhat,factor,elist)
                Kg_csc = Kg_coo.tocsc()
                Ch = Ug.T @ Kg_csc @ Ug
                gamma = Ch[2,2]/Ch[0,0]
                nuhat = 1-2*Ch[2,2]/Ch[0,0]
                Eyhat = 4*Ch[2,2]*(Ch[0,0]-Ch[2,2])/Ch[0,0]
                if nuhat > nuval:
                    eta_nu = max([nuhat - nuval - nuvar, 0.0])
                else:
                    eta_nu = min([nuhat - nuval + nuvar, 0.0])
                nu_g = nuval + eta_nu
                fnu_g = (nuhat-nu_g)**2
                vol = sum(x)/N
                obj = fnu_g + beta*vol
                fEy = Eyhat-Eymin
                eta_Ey = max([fEy - Eyvar, 0.0])
            else:
                fEy = fEy_test
                eta_Ey = eta_Ey_test
            t1 = perf_counter()
            time_array[1] += (t1-t0)
            
            # CGS analysis for dCh
            t0 = perf_counter()
            cgs(dC00_0,dC11_0,dC22_0,dC00_1,dC11_1,dC22_1,dC00_2,dC11_2,dC22_2,
                x,N,sym,etype,aug_etype,inci,Ug,dKe,P,Kr,dKelist)
            t1 = perf_counter()
            time_array[2] += (t1-t0)
            
            # WS analysis for dCh
            t0 = perf_counter()
            dC00_w, dC11_w, dC22_w = ws(x,aug_etype,sym,P,factor,inci,Ug,Hlist)
            t1 = perf_counter()
            time_array[3] += (t1-t0)
            
            # post-procedures
            t0 = perf_counter()
            # sensitivity analysis for obj and Ey
            for e in range(N):
                ggamma = (Ch[2,2]+dC22_w[e])/(Ch[0,0]+dC00_w[e])
                dnu = 2.0*(gamma*dC00_w[e]-dC22_w[e])/(Ch[0,0]+dC00_w[e])
                dEy = 4.0*(1.0-ggamma)*dC22_w[e] + 2.0*Ch[2,2]*dnu
                dobj = 2.0*(nuhat-nu_g)*dnu + dnu*dnu
                if x[e]:
                    s_Ey[e]  = -dEy
                    s_obj[e] = -dobj
                else:
                    s_Ey[e]  =  dEy
                    s_obj[e] =  dobj
            raw_obj = s_obj + beta/N
            obj = fnu_g + beta*vol
            fil_Ey  = Sf @ s_Ey
            fil_obj = Sf @ raw_obj
            mom_obj = momentum*mom_obj + (1.0-momentum)*fil_obj/max(abs(fil_obj))
            mom_obj = mom_obj/max(abs(mom_obj))
            # store data
            list_ptr2inp += [ptr]
            list_top     += [x.copy()]
            list_dis_xx  += [Ug[:,0].copy()]
            list_dis_yy  += [Ug[:,1].copy()]
            list_dis_xy  += [Ug[:,2].copy()]
            list_dC00_0  += [dC00_0.copy()]
            list_dC00_1  += [dC00_1.copy()]
            list_dC00_2  += [dC00_2.copy()]
            list_dC00_w  += [dC00_w.copy()]
            list_dC11_0  += [dC11_0.copy()]
            list_dC11_1  += [dC11_1.copy()]
            list_dC11_2  += [dC11_2.copy()]
            list_dC11_w  += [dC11_w.copy()]
            list_dC22_0  += [dC22_0.copy()]
            list_dC22_1  += [dC22_1.copy()]
            list_dC22_2  += [dC22_2.copy()]
            list_dC22_w  += [dC22_w.copy()]
            list_nu      += [nuhat]
            list_Ey      += [Eyhat]
            list_vol     += [vol]
            # stopping criterion
            if ((obj<(1.0-small)*obj_opt) or (abs(nuhat-nuval)<(1.0-small)*abs(nu_opt-nuval))) and (Eyhat>Eymin-small):
                waiting = 0
                obj_opt = obj
                if (abs(nuhat-nuval)<(1.0-small)*abs(nu_opt-nuval)):
                    # update optimized topology
                    top_opt = x.copy()
                    nu_opt  = nuhat
                    Ey_opt  = Eyhat
            else:
                waiting += 1
                # check convergence
                if waiting == patience:
                    keep_going = False
            t1 = perf_counter()
            time_array[4] += (t1-t0)

        # remove remaining islands from optimized topology
        y = top_opt.copy()
        voly = sum(y)/N
        continent = np.zeros(N,dtype=bool)
        for e in (list(range(0,Ns))+list(range(Ns,N,Ns))):
            if y[e] and (not continent[e]):
                continent = np.zeros(N,dtype=bool)    
                visit(e,y,continent,neighbors)
                continent_vol = sum(continent)/N
            if continent_vol > 0.50*voly:
                break
        if continent_vol > 0.50*voly:
            islands = np.argwhere(y!=continent).ravel()
            y[islands] = False
        if any(top_opt!=y):
            if any(x!=y):
                elist = list(np.argwhere(x!=y)[:,0])
                Ug, Kr = update(x,etype,sym,pk,Ketvec,P,Kg_coo,Zr,shift,Uhat,factor,elist)
                Kg_csc = Kg_coo.tocsc()
                Ch = Ug.T @ Kg_csc @ Ug
                gamma = Ch[2,2]/Ch[0,0]
                nuhat = 1-2*Ch[2,2]/Ch[0,0]
                Eyhat = 4*Ch[2,2]*(Ch[0,0]-Ch[2,2])/Ch[0,0]
            top_opt = x.copy()
            nu_opt = nuhat
            Ey_opt = Eyhat

        # write in log
        tlog.write(' ({:4d} x ):'.format(it))
        time_array[5] = sum(time_array[:5])
        time_array[:5] = time_array[:5]/it
        time_array[6] = (1+small)*it
        tlog.write(' {:6.3f} s : {:6.3f} s : {:6.3f} s : {:6.3f} s : {:6.3f} s ||'.format(
                    time_array[0],time_array[1],time_array[2],time_array[3],time_array[4]))
        tlog.write(' {:7.1f} s\n'.format(time_array[5]))
        iolog.write(' {:5.2f} :'.format(nu_opt))
        iolog.write(' {:5.3f} ||'.format(Ey_opt))
        iolog.write(begin)
        iolog.write(datetime.now().strftime(' %y/%m/%d-%H:%M:%S\n'))

        # store data
        list_top_opt += [top_opt.copy()]
        list_nu_opt  += [nu_opt]
        list_Ey_opt  += [Ey_opt]
        list_tim     += [time_array.copy()]
        
        # update pointer
        ptr += 1
        
        # prepare to open next input file
        fid += 1
        
    #%% Write files
    size_list = len(list_ptr2inp)
    list_ptr2opt += [size_list]
    
    # save files
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/fid.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_fid,dtype=np.uint32))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/inp.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_inp,dtype=np.float32))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/top_opt.npy'.format(
        fid_ini,fid_lim-1,file),np.packbits(np.array(list_top_opt),axis=1))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/nu_opt.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_nu_opt,dtype=np.float32))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/Ey_opt.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_Ey_opt,dtype=np.float32))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/ptr2opt.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_ptr2opt,dtype=np.uint32))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/ptr2inp.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_ptr2inp,dtype=np.uint32))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/top.npy'.format(
        fid_ini,fid_lim-1,file),np.packbits(np.array(list_top),axis=1))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/dis_xx.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_dis_xx,dtype=np.float32))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/dis_yy.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_dis_yy,dtype=np.float32))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/dis_xy.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_dis_xy,dtype=np.float32))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/dC00_0.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_dC00_0,dtype=np.float32))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/dC00_1.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_dC00_1,dtype=np.float32))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/dC00_2.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_dC00_2,dtype=np.float32))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/dC00_w.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_dC00_w,dtype=np.float32))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/dC11_0.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_dC11_0,dtype=np.float32))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/dC11_1.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_dC11_1,dtype=np.float32))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/dC11_2.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_dC11_2,dtype=np.float32))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/dC11_w.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_dC11_w,dtype=np.float32))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/dC22_0.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_dC22_0,dtype=np.float32))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/dC22_1.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_dC22_1,dtype=np.float32))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/dC22_2.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_dC22_2,dtype=np.float32))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/dC22_w.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_dC22_w,dtype=np.float32))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/nu.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_nu,dtype=np.float32))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/Ey.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_Ey,dtype=np.float32))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/vol.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_vol,dtype=np.float32))
    np.save('./output/run_{:05d}_{:05d}/file_{:04d}/tim.npy'.format(
        fid_ini,fid_lim-1,file),np.array(list_tim,dtype=np.float32))
    
    del list_fid, list_inp, list_top_opt, list_nu_opt, list_Ey_opt, list_ptr2opt, list_ptr2inp, list_top
    del list_dis_xx, list_dis_yy, list_dis_xy, list_dC00_0, list_dC00_1, list_dC00_2, list_dC00_w
    del list_dC11_0, list_dC11_1, list_dC11_2, list_dC11_w, list_dC22_0, list_dC22_1, list_dC22_2, list_dC22_w
    del list_nu, list_Ey, list_vol, list_tim
    gc.collect()
    
    # prepare to write next output file
    file += 1
    
#%% close log files
iolog.close()
tlog.close()
print('done!')
