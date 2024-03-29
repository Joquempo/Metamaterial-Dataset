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

import numpy as np
from scipy.sparse import coo_matrix

def get_sfil(N,sym,elepos,Q,rsen):
    clist = []
    csize = np.ndarray((N),dtype=np.uint32)
    for e in range(N):
        et = sym[e,0]
        c = np.argwhere(np.sum((elepos[et,:]-elepos)**2,axis=1) <= rsen**2)
        clist = clist + [c[:,0]]
        csize[e] = len(c)
    size = sum(csize)
    row = np.ndarray((size),dtype=np.uint32)
    col = np.ndarray((size),dtype=np.uint32)
    data = np.ndarray((size))
    i = 0
    for e in range(N):
        et = sym[e,0]
        c = clist[e]
        num = csize[e]
        weights = rsen - np.linalg.norm(elepos[et,:]-elepos[c,:],axis=1)
        weights = weights/sum(weights)
        row[i:i+num] = np.repeat(e,num)
        col[i:i+num] = c
        data[i:i+num] = weights
        i = i + num
    Sf = coo_matrix((data,(row,col)),shape=(N,10*N))
    Sf = Sf.tocsr()
    Sf = Sf @ Q
    return Sf

def get_mope(N,sym,elepos,Q,rmor):
    clist = []
    csize = np.ndarray((N),dtype=np.uint32)
    for e in range(N):
        et = sym[e,0]
        c = np.argwhere(np.sum((elepos[et,:]-elepos)**2,axis=1) <= rmor**2)
        clist = clist + [c[:,0]]
        csize[e] = len(c)
    size = sum(csize)
    row = np.ndarray((size),dtype=np.uint32)
    col = np.ndarray((size),dtype=np.uint32)
    data = np.ndarray((size))
    i = 0
    for e in range(N):
        et = sym[e,0]
        c = clist[e]
        num = csize[e]
        row[i:i+num] = np.repeat(e,num)
        col[i:i+num] = c
        data[i:i+num] = 1.0
        i = i + num
    Mf = coo_matrix((data,(row,col)),shape=(N,10*N))
    Mf = Mf.tocsr()
    Mf = Mf @ Q
    return Mf