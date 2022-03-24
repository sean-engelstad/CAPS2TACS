#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Jan 11 13:54:34 2022

@author: sengelstad6
"""

import os
import numpy as np
import pyCAPS
import matplotlib.pyplot as plt

class Caps2Tacs:
    def __init__(self, csmFile, capsFunction, pytacsFunction, desvars):

        #order of design variables used for optimization
        self.desvars = desvars

        self.ngeomDV = 0
        self.nthickDV = 0
        for desvar in self.desvars:
            if ("thick" in desvar):
                self.nthickDV += 1
            else:
                self.ngeomDV += 1
        self.nvar = self.ngeomDV + self.nthickDV
        
        if (self.nthickDV == 0):
            self.hasThickDVs = False
        else:
            self.hasThickDVs = True

        #initialize the tacs and egads aims
        self.initAims(csmFile, capsFunction)
        
        #store the run pytacs method
        self.pytacsFunction = pytacsFunction
        
        #specify the run settings
        self.reorder = True #reorder nodes
        self.printNodes = True #print nodes in the sens file
        
    def initAims(self, csmFile, capsFunction):
        #initialize egads and tacs AIMs and store them in self.tacs and self.egads
        
        #initialize the caps analysis object 
        self.caps = pyCAPS.Problem('myCAPS', capsFile=csmFile, outLevel=0)
        
        #store caps geometry and desvars
        self.geom = self.caps.geometry
        self.nvar = len(self.desvars)
        
        #generate egads aim
        self.egads = self.caps.analysis.create(aim="egadsTessAIM")
        
        #generate tacs aim
        self.tacs = self.caps.analysis.create(aim = "tacsAIM", name = "tacs")
        
        #put design variables into tacsAim
        # I now require you to put in the design variable dict
        # make sure the same order as your x vector
#        desDict = {}
#        for key in self.desvars:
#            desDict[key] = {}
#        self.tacs.input.Design_Variable = desDict
        
        #run the setupFunction to set mesh, geom, load, mat prop settings 
        #in egads and tacs aims
        capsFunction(self.egads, self.tacs)
        
    def solveStructuralProblem(self, D=None):
        #to solve each structural problem, we update our design variables
        #then we run TACS, compute its func and sens
        #then print & combine sensitivities with caps to get df/dD and store them
        

        #update design with new desvar
        if (not(D is None)):
            design = self.makeDesignDict(D)
            self.updateDesign(design)
        
        #run TACS with new design
        self.runTACS()
        
        #print sensitivity files to caps and combine them
        self.combineSensitivities()
        
        #store results - function values and full sensitivity
        self.storeResults()
    
    def buildMesh(self, D=None):
    	#used if you just want to build the .bdf and .dat files
    	#for the mesh for this design
    
    	#update design with new desvar
        if (not(D is None)):
            design = self.makeDesignDict(D)
            self.updateDesign(design)
            
        #run the tacs preanalysis
        self.tacs.preAnalysis()
        
    def makeDesignDict(self, D):
        D = np.array(D)
        designDict = {}
        for i in range(len(D)):
            designDict[self.desvars[i]] = D[i]
        return designDict

    #methods to be called outside of the class
    def makeThicknessDV(self, capsGroup, thickness):
        desvar    = {"groupName" : capsGroup,
              "initialValue" : thickness,
              "lowerBound" : thickness*0.5,
              "upperBound" : thickness*1.5,
              "maxDelta"   : thickness*0.1}
        return desvar
    def makeThicknessDVR(self, DVname):
        DVR = {"variableType": "Property",
        "fieldName" : "T",
        "constantCoeff" : 0.0,
        "groupName" : DVname,
        "linearCoeff" : 1.0}
        return DVR

    def updateDesign(self, designDict, output=False):
        #print out the design variables
        if (output): print("Design variables: ", designDict)

        #use DesignVarRelations or DesignVariable
        useDVR = True

        #grab the property dictionary to edit thick DVs
        propDict = self.tacs.input.Property
        
        #grab the DVR dict if using relations
        if (useDVR and self.hasThickDVs):
            DVRdict = self.tacs.input.Design_Variable_Relation
        
        #grab the DVdict to update it
        DVdict = self.tacs.input.Design_Variable

        #loop over each design variable
        for deskey in designDict:

            #assume all thickness desvars are thick##
            if ("thick" in deskey):
                thickness = designDict[deskey]

                if (useDVR):
                    capsGroup = DVRdict[deskey]["groupName"]
                    DVRdict[deskey] = self.makeThicknessDVR(deskey)

                #also update the DVs too
                capsGroup = DVdict[deskey]["groupName"]
                DVdict[deskey] = self.makeThicknessDV(capsGroup,thickness)

                
                propDict[capsGroup]["membraneThickness"] = thickness

            #otherwise it's a geometric desvar
            else:
                self.geom.despmtr[deskey].value = designDict[deskey]
            
        #update the new property dictionary w/ thicknesses
        self.tacs.input.Property = propDict

        #update DVR dictionary
        if (useDVR and self.hasThickDVs): self.tacs.input.Design_Variable_Relation = DVRdict
        
        #update DV dictionary
        self.tacs.input.Design_Variable = DVdict

        print("design dict: ",designDict)
        print("updated design")
    
    def runTACS(self):
        #build the BDF and data file with CAPS preanalysis
        #then run pytacs on the data file, computing func, sens
        
        #run tacs aim preanalysis to generate BDF and DAT file
        self.tacs.preAnalysis()
        print("ran preanalysis()")
        
        #read the data file
        datFile = os.path.join(self.tacs.analysisDir, self.tacs.input.Proj_Name + '.dat')
        
        #compute function values and sensitivities for each SP in your Pytacs method
        self.pytacsFunction(self, datFile)

        print("ran pytacs()")
        
    def combineSensitivities(self):
        
        #run bookkeeping data to get nfunc, nnodes, etc.
        self.bookKeeping()
        
        #reorder the nodes from pytacs, thus rearranging Xpts sensitivity
        if (self.reorder): self.reorderNodes()
        
        #print the sensitivities from tacs to CAPS
        self.printSensitivity()
    
    def reorderNodes(self):
        #as the nodes get reordered via MPI when running pytacs
        #we have to reorder them back using the tacsNodeMap
        #this changes the final sensitivities
        
        #loop over each function that was evaluated to change sensitivities for that
        for key in self.funcKeys:
            
            #get sensitivity w.r.t. mesh aka Xpts sens
            dfdX = self.sens[key]['Xpts']
            dfdX = dfdX.reshape((self.nnodes,3))
            
            #thickness desvars, no reordering for this
            #dfdH = self.pytacsSens[key]['struct']
            
            #get the nodemap from pytacs
            bdfNodes = np.linspace(1.0,self.nnodes,self.nnodes)
            tacsNodeMap = self.FEASolver.meshLoader.getLocalNodeIDsFromGlobal(bdfNodes,nastranOrdering=True)
            
            #reorder nodes as dfdX_bdf
            dfdX_bdf = np.zeros((self.nnodes,3))
            for bdfind in range(self.nnodes):
                    #node 1 in bdf, corresponds to 0 bdfind, and plug in bdfind-1 to get tacsInd cause cyclically off by 1
                    tacsInd = tacsNodeMap[bdfind] #tacsNodeMap is cyclically off by 1
                    dfdX_bdf[bdfind,:] = dfdX[tacsInd,:]
            
            #update function sensitivity
            self.sens[key]['Xpts'] = dfdX_bdf
        
    def bookKeeping(self):
        #get bookkeeping info on func such as funcKeys, nfunctions, nnodes
        self.funcKeys = self.func.keys()
        self.nfunc = len(self.funcKeys)
        funcKeysList = list(self.funcKeys)
        
        #get number of nodes
        subSens = self.sens[funcKeysList[0]]['Xpts']
        self.nnodes = int(len(subSens)/3)
        #print(self.nnodes)
    
    def printSensitivity(self):
        #print our dfdX sensitivity to CAPS
        #then this will be combined to produce df/dD sensitivity
        #available in dynout variables
        
        #where to print .sens file
        sensFilename = os.path.join(self.tacs.analysisDir, self.tacs.input.Proj_Name+".sens")
        
        #open the file
        with open(sensFilename, "w") as f:
            
            #write (nfunctions) in first line
            f.write("{}\n".format(self.nfunc))
            
            #for each function mass, stress, etc.
            for key in self.funcKeys:
                
                #get the pytacs/tacs sensitivity w.r.t. mesh for that function
                cSens = self.sens[key]['Xpts']
                
                #write the key,value,nnodes of the function
                f.write(key + "\n")
                f.write("{}\n".format(self.func[key]))
                f.write("{}\n".format(self.nnodes))

                #for each node, print nodeind, dfdx, dfdy, dfdz for that mesh element
                for nodeind in range(self.nnodes): # d(Func1)/d(xyz)
                    
                    #if we print nodes first, then print them
                    if (self.printNodes):
                        bdfind = nodeind + 1
                        f.write("{} {} {} {}\n".format(bdfind, cSens[nodeind,0], cSens[nodeind,1], cSens[nodeind,2]))
                    #otherwise just print df/dx, df/dy, df/dz for each element
                    else:
                        f.write("{} {} {}\n".format(cSens[nodeind,0], cSens[nodeind,1], cSens[nodeind,2]))
        
        #run aim postanalysis
        self.tacs.postAnalysis()
        print("ran postAnalysis()")
    
    def storeResults(self):
        #store the function and full df/dD sensitivities from CAPS aim dynout attributes
        #initialize gradient variable
        self.func = {}
        self.grad = {}
        print("starting store Results")
        #loop over each pytacs function
        for key in self.funcKeys:
            print("starting function: ",key)
            self.func[key] = self.tacs.dynout[key].value
            self.grad[key] = np.zeros((self.nvar))
            #print(len(self.grad[key]))

            #loop over each design variable to get the full df/dD gradient
            print("struct sens: ",self.sens[key]['struct'])
            ind = 0
            thickind = 0
            for desvar in self.desvars:
                print("storing desvar: ",key, ", ", desvar)
                if ("thick" in desvar):
                    #use struct here, #print(self.sens[key]['struct'])
                    #struct includes geomDVs in same order
                    self.grad[key][ind] = self.sens[key]['struct'][ind]
                    thickind += 1
                else:
                    self.grad[key][ind] = self.tacs.dynout[key].deriv(desvar)
                ind += 1
        #print("finished storing results\n")
        #print(self.grad)

    def printDesignVariables(self, desvar):
        ind = 0
        for desvarName in self.desvars:
            print("{}: {}".format(desvarName, desvar[ind]))
            ind += 1

    def printResults(self):
        for key in self.funcKeys:
            print("function {}, value {}".format(key, self.func[key]))
            print("gradient {}, value {}".format(key, self.grad[key]))

    def checkGradients(self, x, functions, gradients, names,h=1e-4):
    	#computes finite difference check for functions and gradients
    	#that you provide to it
    
        #Example:
        #functions = [self.mass,self.vm_stress]
        #gradients = [self.mass_grad, self.vm_stress_grad]
        #names = ["mass","ks_vm_stress"]
        #x = np.ones(2)
            #x = np.ones((self.nvar))
        
        nfuncs = len(functions)
        
        errors = []
        for i in range(nfuncs):
            myfunc = functions[i]
            mygrad = gradients[i]
            name = names[i]
            
            x = np.array(x)        
            p = np.random.uniform(size=x.shape)
            p = p / np.linalg.norm(p)
            
            func1 = myfunc(x-p*h)
            func2 = myfunc(x+p*h)
            
            fdGrad = (func2-func1)/2/h
            cgrad = mygrad(x)
            print("func {}, grad {}".format(name, cgrad))
            #print(mygrad,p)
            directDeriv = np.dot(cgrad, p)
            
            error = (directDeriv - fdGrad)/fdGrad
            errors.append(error)
        for i in range(nfuncs):
            name = names[i]; error = errors[i]
            print(name + ' FD gradient error',error)
                

        # for fi in range(nfuncs):
        #     name = names[i]; error = errors[i]
        #     string1 = 'd' + name + '_exact'
        #     string2 = 'd' + name + 'pred'
        #     print(name + ' FD gradient error',error)

        #     fig, ax = plt.subplot(nfuncs,1,fi)
        #     ax.loglog(hvec,dfExact,'b-',label=string1)
        #     ax.loglog(hvec, dfPred,'g-',label=string2)
        #     ax.xlabel('step size h in deltaD')
        #     ax.ylabel('change in f(D)')
        #     ax.legend()
        # plt.show()


def getThicknessDV(capsGroup, thickness):
    desvar    = {"groupName" : capsGroup,
          "initialValue" : thickness,
          "lowerBound" : thickness*0.5,
          "upperBound" : thickness*1.5,
          "maxDelta"   : thickness*0.1}
    return desvar
def getThicknessDVR(DVname):
    DVR = {"variableType": "Property",
    "fieldName" : "T",
    "constantCoeff" : 0.0,
    "groupName" : DVname,
    "linearCoeff" : 1.0}
    return DVR