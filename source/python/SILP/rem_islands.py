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
import sys

sys.setrecursionlimit(100000)
def visit(e,x,continent,neighbors):
    continent[e] = True
    for ee in neighbors[e,:]:
        if x[ee] and (not continent[ee]):
            visit(ee,x,continent,neighbors)
    return

def get_neighbors(Ns,inci,inci_lb,inci_bot,sym,sym_lb,sym_bot):
    N = Ns**2
    Mf = 1 + 6*Ns*(Ns+1) + Ns + 4*N
    Nf = 10*N
    finci = np.vstack((inci,inci_lb,inci_bot))
    fsym = np.hstack((sym,sym_lb,sym_bot))
    nodes = -np.ones((Mf,6),dtype=int)
    counter = np.zeros(Mf,dtype=int)
    for e in range(Nf):
        for k in range(4):
            n = finci[e,k]
            nodes[n,counter[n]] = e
            counter[n] += 1
    neighbors_extended = -np.ones((Nf,4),dtype=int)
    counter = np.zeros(Nf,dtype=int)
    for e in range(Nf):
        for k in range(4):
            n = finci[e,k]
            for kk in range(6):
                ee = nodes[n,kk]
                if (ee != e) and (ee != -1):
                    if ee not in neighbors_extended[e,:]:
                        if len(np.setdiff1d(finci[e,:],finci[ee,:],assume_unique=True)) == 2:
                            neighbors_extended[e,counter[e]] = ee
                            counter[e] += 1
    neighbors_ext = neighbors_extended.copy()
    for ef in range(Nf):
        for k in range(4):
            if neighbors_extended[ef,k] != -1:
                e = np.argwhere(fsym==neighbors_extended[ef,k])[0,0]
                neighbors_ext[ef,k] = e
    neighbors = np.ndarray((N,4),dtype=int)
    for k in range(N):
        neighbors[k,:] = neighbors_ext[fsym[k,0],:]
    return neighbors