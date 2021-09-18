
"""
Author: Jacob Hess 
First Version: January 2021
This Version: September 2021

Description: This code solves the consumption/saving (aka the income flucuation problem) for the infinitely household 
in partial equilibrium using the endogenous grid method. 

To find the stationary distribution one can choose from three methods: 

1) Discrete approximation of the density function which conducts a fixed point iteration with linear interpolation
2) Eigenvector method to solve for the exact stationary density.
3) Monte carlo simulation. 
    
Finally, to evaluate the accuracy of the solution the code computes the euler equation error with two different methods. 
One by simulating the model and calulating the error for each individual. The other is by calculating the error across 
the entire in the state space.

Aknowledgements: I wrote the algorithms using the following resources :
    1) Gianluca Violante's global methods and distribution approximation notes (https://sites.google.com/a/nyu.edu/glviolante/teaching/quantmacro)
    2) Heer and Maussner 2nd ed. Ch. 7
    3) Raul Santaeulalia-Llopis' ABHI Notes (http://r-santaeulalia.net/)
    4) Alexander Ludwig's notes (https://alexander-ludwig.com/)
    5) Jeppe Druedahl (https://github.com/JeppeDruedahl) and NumEconCopenhagen (https://github.com/NumEconCopenhagen)
    
Required packages: 
    -- Packages from the anaconda distribution. (to install for free: https://www.anaconda.com/products/individual)
    -- Interpolation from EconForge
       * optimized interpolation routines for python/numba
       * to install 'conda install -c conda-forge interpolation'
       * https://github.com/EconForge/interpolation.py

Note: If simulation tells you to increase grid size, increase self.sav_max in function setup_parameters.
"""



import time
import numpy as np
from numba import njit, prange
from interpolation import interp
import matplotlib.pyplot as plt
import seaborn as sns
sns.set(style='whitegrid')



#############
# I. Model  #
############


class ConSaveEGMsmall:

    ############
    # 1. setup #
    ############

    def __init__(self, a_bar = 0,              #select borrowing limit
                       plott =1,               #select 1 to make plots
                       simulate =0,            #select 1 to run simulation (if distribution_method = 'monte carlo' simulate is automatically set to 1 )
                       full_euler_error = 0,        #select to compute euler_error for entire state space
                       distribution_method = 'discrete' #Approximation method of the stationary distribution. 
                                                       #Options: 'discrete', 'eigenvector', 'monte carlo' or 'none'
                       ):
        
        #parameters subject to changes
        self.a_bar, self.plott, self.simulate = a_bar, plott, simulate
        self.distribution_method, self.full_euler_error = distribution_method, full_euler_error

        self.setup_parameters()
        self.setup_grid()
        
        #pack parameters for jitted functions
        
        if distribution_method == 'discrete':
             self.params_discrete = self.grid_sav, self.Nz, self.pi, self.pi_stat, self.maxit, self.tol
            
        if self.simulate ==1 or self.distribution_method == 'monte carlo':
            self.params_sim = self.a0, self.z0, self.r, self.w, self.simN, self.simT, self.grid_z, self.grid_sav, \
                self.sigma, self.beta, self.pi, self.seed
        
        #warnings 
        
        # We need (1+r)beta < 1 for convergence.
        assert (1 + self.r) * self.beta < 1, "Stability condition violated."
        
        #We require the borrowing limit to be greater than the natural borriwing limit (or no ponzi condition).
        #The limit is where an agent can borrow and repay it in the next period with probability 1.
        assert self.a_bar + 1e-6 > ((-1) * ((1+self.r)/self.r) * self.grid_z[0]), "Natural borrowing limit violated."
        
        if self.distribution_method != 'discrete' and self.distribution_method != 'eigenvector' and self.distribution_method != 'monte carlo' and self.distribution_method != 'none' :
            raise Exception("Stationary distribution approximation method incorrectly entered: Choose 'discrete', 'eigenvector', 'monte carlo' or 'none' ")
            
        if self.plott != 1 and self.plott != 0:
            raise Exception("Plot option incorrectly entered: Choose either 1 or 0.")
            
        if self.simulate != 1 and self.simulate != 0:
            raise Exception("Simulate option incorrectly entered: Choose either 1 or 0.")
            
        if self.full_euler_error != 1 and self.full_euler_error != 0:
            raise Exception("Euler error full grid evaluation option incorrectly entered: Choose either 1 or 0.")
        
        
        

    def setup_parameters(self):

        # a. model parameters
        self.sigma=2               #crra coefficient
        self.beta = 0.95  # discount factor
        self.rho = (1-self.beta)/self.beta #discount rate
    
        # prices 
        self.w=1 
        self.r=0.04

        # b. iteration parameters
        self.tol = 1e-6  # tolerance for iterations
        self.maxit = 2000  # maximum number iterations
        
        # c. hh solution

        # income
        self.Nz = 2
        self.grid_z = np.array([0.5, 1.5])                #productuvity states
        self.pi = np.array([[3/4, 1/4],[1/4, 3/4]])   #transition probabilities
        

        # savings grid
        self.Ns = 200
        self.sav_min = self.a_bar
        self.sav_max = 25
        self.curv = 3
        
        # d. simulation
        if self.simulate or self.distribution_method == 'monte carlo':
            self.seed = 123
            self.simN = 50_000  # number of households
            self.simT =  2000 # number of time periods to simulate
            self.sim_burnin = 1000  # burn-in periods before calculating average savings
            self.init_asset = 1.0  # initial asset (homogenous)
            self.a0 = self.init_asset * np.ones(self.simN)  #initial asset for all individuals
            

        


    def setup_grid(self):

        # a. savings (or end-of-period assets) grid
        self.grid_sav = self.make_grid(self.sav_min, self.sav_max, self.Ns, self.curv)  
        
        # b. stationary distribution of markov chain
        self.pi_stat = self.stationary_mc(self.pi)
        
        # c. initial distribution of z for simulation and ensure produtcity grid sums to one
        z_diag = np.diag(self.pi ** 1000)
        self.ini_p_z = z_diag / np.sum(z_diag)
        
        avg_z = np.sum(self.grid_z * self.ini_p_z)
        self.grid_z = self.grid_z / avg_z  # force mean one
        
        # d. initial income shock drawn for each individual from initial distribution
        if self.simulate or self.distribution_method == 'monte carlo':
            self.z0 = np.zeros(self.simN, dtype=np.int32)
            self.z0[np.linspace(0, 1, self.simN) > self.ini_p_z[0]] = 1
        
        



    #######################
    # 2. helper functions #
    ######################
    
    def make_grid(self, min_val, max_val, num, curv):  
        """
        Makes an exponential grid of degree curv. 
        
        A higher curv will put more points closer a_min. 
        
        Equivalenty, np.linspace(min_val**(1/curv), max_val**(1/curv), num)**curv will make
        the exact same grid that this function does.
        """
        grd = np.zeros(num)
        scale=max_val-min_val
        grd[0] = min_val
        grd[num-1] = max_val
        for i in range(1,num-1):
            grd[i] = min_val + scale*((i)/(num - 1)) ** curv
        
        return grd

    def u_prime(self, c) :
        eps = 1e-8
        
        return np.fmax(c, eps) ** (-self.sigma)

    
    def u_prime_inv(self, x):    
        eps = 1e-8
        
        return np.fmax(x, eps) ** (-1/self.sigma)
    

    def stationary_mc(self, pi):
        """
        Returns the stationary/ergodic distribution of the markov chain.
        
        *Input
            - pi: markov chain transition matrix
            
        *Output
            - stataionary distribution of the markov chain
        """
        
        p = np.copy(pi)  #create a copy of pi 
        nrows,ncols = p.shape
        for i in range(nrows):
            p[i,i] = p[i,i]-1
        
        q = p[:,0:nrows-1]
        # appends column vector
        q = np.c_[q,np.ones(nrows)]  
        x = np.zeros(nrows-1)
        # appends element in row vector
        x = np.r_[x,1]
        
        return np.dot(x, np.linalg.inv(q))
    
    
    
    
    
    ############################################
    # 3. Household and Endogenous Grid Method #
    ###########################################
     
    def egm_algo(self, pol_cons_old):
           
        """
        Endogenous grid method to help solve the household problem.
        
        *Input
            - pol_cons_old: consumption policy function from previous iteration.
            
        *Output
            - pol_cons: updated consumption policy function
            - a_star: endogenous grid 
        """
        
        # a. initialize 
        c_tilde=np.empty((self.Nz, self.Ns))
        a_star=np.empty((self.Nz, self.Ns))
        pol_cons = np.empty((self.Nz, self.Ns))
        
        for i_z in range(self.Nz):
 
            # b. find RHS of euler equation (step 3 in EGM algo)
            avg_marg_u_plus = np.zeros(self.Ns)
            
            for i_zz in range(self.Nz):
 
                # i. future consumption
                c_plus = pol_cons_old[i_zz,:]
 
                # iii. future marginal utility
                marg_u_plus = self.u_prime(c_plus)
 
                # iv. average marginal utility
                weight = self.pi[i_z, i_zz]
 
                avg_marg_u_plus += weight * marg_u_plus
                
            ee_rhs = (1 + self.r) * self.beta * avg_marg_u_plus    
 
            # c. find current consumption (step 4 EGM algo)
            c_tilde[i_z,:] = self.u_prime_inv(ee_rhs)
            
            # d. get the endogenous grid of the value of assets today (step 5 EGM algo) 
            a_star[i_z,:] = (c_tilde[i_z,:] + self.grid_sav - self.grid_z[i_z]*self.w) / (1+self.r)
            
            # e. update new consumption policy guess on savings grid
            for i_s, v_s in enumerate(self.grid_sav):
                
                if v_s <= a_star[i_z,0]:   #borrowing constrained, outside the grid range on the left
                    pol_cons[i_z, i_s] = (1+self.r)*v_s + self.grid_sav[0] + self.grid_z[i_z]*self.w
                    
                elif  v_s >= a_star[i_z,-1]: # , linearly extrapolate, outside the grid range on the right
                    pol_cons[i_z, i_s] = c_tilde[i_z,-1] + (v_s-a_star[i_z,-1])*(c_tilde[i_z,-1] - c_tilde[i_z,-2])/(a_star[i_z,-1]-a_star[i_z,-2])
 
                else: #linearly interpolate, inside the grid range
                    pol_cons[i_z, i_s] = interp(a_star[i_z,:], c_tilde[i_z,:], v_s)
                    
        # f. set any values below borrowing constraint to sav_min
        a_star[a_star<self.grid_sav[0]] = self.grid_sav[0]             
        
        
        return pol_cons, a_star             
    

   
    def solve_hh(self):
        
        """
        Solves the household problem.
        
        *Output
            - pol_cons: consumption policy function solution given prices
            - pol_sav: savings policy function solution given prices
            - a_star: endogenous grid
            - it_hh: number of iterations to convergence
        """
    
        # a. initialize and initial guess (consume everything. Step 2 in EGM algo)
        
        pol_cons_old = np.empty((self.Nz, self.Ns))
        
        for i_z, v_z in enumerate(self.grid_z):
            pol_cons_old[i_z,:] = (1+self.r)*self.grid_sav + v_z*self.w 

        # b. policy function iteration
        
        for it_hh in range(self.maxit):
            
            # i. iterate
            pol_cons, a_star = self.egm_algo(pol_cons_old)
            
            # ii. calculate supremum norm
            dist = np.abs(pol_cons - pol_cons_old).max()
            
            if dist < self.tol :
                break
            
            pol_cons_old = np.copy(pol_cons)

        # c. obtain savings policy function
        pol_sav = np.empty([self.Nz, self.Ns])
        
        for i_z, v_z in enumerate(self.grid_z):
            pol_sav[i_z,:] = (1+self.r)*self.grid_sav + v_z*self.w - pol_cons[i_z,:]
            
        
        return pol_cons, pol_sav, a_star, it_hh



    ######################################
    # 4. Euler Equation Error Analysis  #
    #####################################
    

    def ee_error(self):
        """
        Computes the euler equation error over the entire state space with a finer grid.
        
        *Output
            * Log10 euler_error
            * max Log10 euler error
            * average Log10 euler error
        """
        
                
        # a. initialize
        euler_error = np.zeros((self.Nz, self.Ns))
        
        # b. helper function
        u_prime = lambda c : c**(-self.sigma)
        
        u_prime_inv = lambda x : x ** (-1/self.sigma)
        
        # c. calculate euler error at all fine grid points
        
        for i_z, z in enumerate(self.grid_z):       #current income shock
            for i_s, s0 in enumerate(self.grid_sav):   #current asset level
                
                # i. interpolate savings policy function fine grid point
            
                a_plus = interp(self.grid_sav, self.pol_sav[i_z,:], s0)
                
                # liquidity constrained, do not calculate error
                if a_plus <= 0:     
                    euler_error[i_z, i_s] = np.nan
                
                # interior solution
                else:
                    
                    # ii. current consumption and initialize expected marginal utility
                    c = (1 + self.r) * s0 + self.w * z - a_plus
                    
                    avg_marg_c_plus = 0
                    
                    # iii. expected marginal utility
                    for i_zz, z_plus in enumerate(self.grid_z):      #next period productivity
                    
                        c_plus = (1 + self.r) * a_plus + self.w * z_plus - interp(self.grid_sav, self.pol_sav[i_zz,:], a_plus)
                        
                        #expectation of marginal utility of consumption
                        avg_marg_c_plus += self.pi[i_z,i_zz] * u_prime(c_plus)
                    
                    # iv. compute euler error
                    euler_error[i_z, i_s] = 1 - u_prime_inv(self.beta*(1+self.r)*avg_marg_c_plus) / c
                    
       
        # ii. transform euler error with log_10. take max and average
        euler_error = np.log10(np.abs(euler_error))
        max_error =  np.nanmax(np.nanmax(euler_error, axis=1))
        avg_error = np.nanmean(euler_error) 
        
        
        
        return euler_error, max_error, avg_error
        

 
    
    
    #####################################################
    # 5. Stationary Distribution: Eigenvector Method   #
    ####################################################
    
    def eigen_stationary_density_egm(self):
        """
        Solve for the exact stationary density. First constructs the Nz*Ns by Nz*Ns transition matrix Q(a',z'; a,z) 
        from state (a,z) to (a',z'). Then obtains the eigenvector associated with the unique eigenvalue equal to 1. 
        This eigenvector (renormalized so that it sums to one) is the unique stationary density function.
        
        Note: About 99% of the computation time is spend on the eigenvalue calculation. For now there is no
        way to speed this function up as numba only supports np.linalg.eig() when there is no domain change 
        (ex. real numbers to real numbers). Here there is a domain change as some eigenvalues and eigenvector 
        elements are complex.

        *Output
            * stationary_pdf: stationary density function
            * Q: transition matrix
        """
        
        # a. initialize transition matrix
        Q = np.zeros((self.Nz*self.Ns, self.Nz*self.Ns))
        
        # b. interpolate and construct transition matrix 
        for i_z in range(self.Nz):    #current productivity 
            for i_s, s0 in enumerate(self.grid_sav):    #savings grid point
                
                # i. interpolate
                s_intp = np.interp(s0, self.a_star[i_z,:], self.grid_sav)    #use np.interp instead of interp to deal with 'division by zero' problem.
                
                #take the grid index to the right. s_intp lies between grid_sav_[j-1] and grid_sav[j].
                j = np.sum(self.grid_sav <= s_intp)  
                
                #less than or equal to lowest grid value
                if s_intp <= self.grid_sav[0]:
                    p = 0
                    
                #more than or equal to greatest grid value
                elif s_intp >= self.grid_sav[-1]:
                   p = 1
                   j = j-1 #since right index is outside the grid make it the max index
                   
                #inside grid
                else:
                   p = (s0 - self.a_star[i_z, j-1]) / (self.a_star[i_z, j] - self.a_star[i_z, j-1])
                    
                # ii. transition matrix
                ns = i_z*self.Ns    #minimum row index
                
                for i_zz in range(self.Nz):     #next productivity state
                    ms = i_zz * self.Ns     #minimum column index
                    
                    Q[ns + i_s, ms + j]= p * self.pi[i_z, i_zz]
                    Q[ns + i_s, ms + j - 1] = (1.0-p)* self.pi[i_z, i_zz]
        
        # iii. ensure that the rows sum up to 1
        assert np.allclose(Q.sum(axis=1), np.ones(self.Nz*self.Ns)), "Transition matrix error: Rows do not sum to 1"
        
        
        
        # c. get the eigenvector 
        eigen_val, eigen_vec = np.linalg.eig(Q.T)    #transpose Q for eig function.
        
        
        
        # i. find column index for eigen value equal to 1
        idx = np.argmin(np.abs(eigen_val-1.0))
        
        eigen_vec_stat = np.copy(eigen_vec[:,idx])
        
        
        
        # ii. ensure complex arguments of any complex numbers are small and convert to real numbers
        
        if np.max(np.abs(np.imag(eigen_vec_stat))) < 1e-6:
            eigen_vec_stat = np.real(eigen_vec_stat)  # drop the complex argument of any complex numbers. 
            
        else:
            raise Exception("Stationary eigenvector error: Maximum complex argument greater than 0.000001. Use a different distribution solution method.")
        
        
        # d. obtain stationary density from stationary eigenvector
        
        # i. reshape
        stationary_pdf = eigen_vec_stat.reshape(self.Nz,self.Ns)
        
        # ii. stationary distribution by percent 
        stationary_pdf=stationary_pdf/np.sum(np.sum(stationary_pdf,axis=0)) 
        
        return stationary_pdf, Q
        
    
    
    #####################
    # 6. Main Function  #
    #####################

    def solve_model(self):
    
        """
        Runs the entire model.
        """    
        
        t0 = time.time()    #start the clock
        
        
        
        # a. solve household problem 
        print("\nSolving household problem...")
        
        self.pol_cons, self.pol_sav, self.a_star, self.it_hh = self.solve_hh()
        
        if self.it_hh < self.maxit-1:
            print(f"Policy function convergence in {self.it_hh} iterations.")
        else : 
            raise Exception("No policy function convergence.")
        
        t1 = time.time()
        print(f'Household problem time elapsed: {t1-t0:.2f} seconds')
        
        
        
        # b. stationary distribution
        
        # discrete approximation
        if self.distribution_method == 'discrete':
            
            print("\nStationary Distribution Solution Method: Discrete Approximation and Forward Iteration on Density Function")
            print("\nComputing...")
            
            # i. approximate stationary density
            self.stationary_pdf, self.it_pdf = discrete_stationary_density_egm(self.pol_sav, self.a_star, self.params_discrete)
            
            if self.it_pdf < self.maxit-1:
                print(f"Convergence in {self.it_pdf} iterations.")
            else : 
                raise Exception("No density function convergence.")
            
            # ii. steady state assets
            self.a_ss = np.sum(np.dot(self.stationary_pdf, self.grid_sav))
            
            # iii. marginal wealth density
            self.stationary_wealth_pdf = np.sum(self.stationary_pdf, axis=0)
            
            t2 = time.time()
            print(f'Density approximation time elapsed: {t2-t1:.2f} seconds')
        
        
        
        # eigenvector
        if self.distribution_method == 'eigenvector':
            
            print("\nStationary Distribution Solution Method: Eigenvector Method for Exact Stationary Density")
            print("\nComputing...")
            
            self.stationary_pdf, self.Q = self.eigen_stationary_density_egm()
        
            # i. aggregate asset holdings
            self.a_ss = np.sum(np.dot(self.stationary_pdf, self.grid_sav))
            
            # iii. marginal wealth density
            self.stationary_wealth_pdf = np.sum(self.stationary_pdf, axis=0)
            
            t2 = time.time()
            print(f'Density computation time elapsed: {t2-t1:.2f} seconds')
            
        
        
        # monte carlo simulation
        if self.simulate ==1 or self.distribution_method == 'monte carlo':
            
            if self.distribution_method == 'monte carlo':
                print("\nStationary Distribution Solution Method: Monte Carlo Simulation")
            
            print("\nSimulating...")
            
            # i. simulate markov chain and endog. variables
            self.sim_c, self.sim_sav, self.sim_z, self.sim_m, self.euler_error_sim = simulate_MarkovChain(
                self.pol_cons,
                self.pol_sav,
                self.params_sim
            )
            
            # ii. steady state assets
            if self.distribution_method == 'monte carlo':
                self.a_ss = np.mean(self.sim_sav[self.sim_burnin :])
            
            # iii. max and average euler error error, ignores nan which is when the euler equation does not bind
            self.max_error_sim =  np.nanmax(self.euler_error_sim)
            self.avg_error_sim = np.nanmean(np.nanmean(self.euler_error_sim, axis=1)) 
            
            t2 = time.time()
            print(f'Simulation time elapsed: {t2-t1:.2f} seconds')
        
        else:
            t2 = time.time()
            
            
            
        # c. calculate euler equation error across the state space
        
        if self.full_euler_error:
            print("\nCalculating Euler Equation Error...")
            
            self.euler_error, self.max_error, self.avg_error = self.ee_error()
            
            t3 = time.time()
            print(f'Euler Eq. error calculation time elapsed: {t3-t2:.2f} seconds')
            
        else: 
            t3 = time.time()
        
        
        
        # d. plot
        
        if self.plott:
            
            print('\nPlotting...')
            
            ##### Solutions #####
            plt.plot(self.grid_sav, self.pol_sav.T)   
            plt.plot(self.grid_sav,self.grid_sav,':')
            plt.title("Savings Policy Function")
            plt.xlabel('Assets')
            plt.ylabel('Savings')
            plt.legend(['z='+str(self.grid_z[0]),'z='+str(self.grid_z[1]),'45 degree line'])
            #plt.savefig('savings_policyfunction_egm_v1.pdf')
            plt.show()
            
            plt.plot(self.grid_sav, self.pol_cons.T)
            plt.title("Consumption Policy Function")
            plt.xlabel('Assets')
            plt.ylabel('Consumption')
            plt.legend(['z='+str(self.grid_z[0]),'z='+str(self.grid_z[1])])
            #plt.savefig('consumption_policyfunction_egm_v1.pdf')
            plt.show()
            
            if self.full_euler_error:
                plt.plot(self.grid_sav, self.euler_error.T)
                plt.title('Log10 Euler Equation Error')
                plt.xlabel('Assets')
                #plt.savefig('log10_euler_error_egm_v1.pdf')
                plt.show()
            
            
            
            ##### Distributions ####
            if self.distribution_method == 'discrete' or self.distribution_method == 'eigenvector':
                
                # joint stationary density
                plt.plot(self.grid_sav, self.stationary_pdf.T)
                plt.title("Joint Stationary Density (Discrete Approx.)") if self.distribution_method == 'discrete' else plt.title("Joint Stationary Density (Eigenvector Method)")
                plt.xlabel('Assets')
                plt.legend(['z='+str(self.grid_z[0]),'z='+str(self.grid_z[1])])
                #plt.savefig('joint_density_egm_v1_discrete.pdf') if self.distribution_method == 'discrete' else plt.savefig('joint_density_egm_v1_eigenvector.pdf')
                plt.show()
                
                # marginal wealth density
                plt.plot(self.grid_sav, self.stationary_wealth_pdf)
                plt.title("Stationary Wealth Density (Discrete Approx.)") if self.distribution_method == 'discrete' else plt.title("Stationary Wealth Density (Eigenvector Method)")
                plt.xlabel('Assets')
                #plt.savefig('wealth_density_egm_v1_discrete.pdf') if self.distribution_method == 'discrete' else plt.savefig('wealth_density_egm_v1_eigenvector.pdf')
                plt.show()
                
            
            
            if self.distribution_method == 'monte carlo':
                sns.histplot(self.sim_sav[-1,:], bins=100, stat='density')
                plt.title("Stationary Wealth Density (Monte Carlo Approx.)")
                plt.xlabel('Assets')
                #plt.savefig('wealth_density_egm_v1_montecarlo.pdf')
                plt.show()
    
            
        
            ##### Simulation #####
            if self.simulate or self.distribution_method == 'monte carlo':
                fig, (ax1, ax2) = plt.subplots(2,1,figsize=(10,6))
                fig.tight_layout(pad=4)
                
                #first individual over first 100 periods
                ax1.plot(np.arange(0,99,1), self.sim_sav[:99,1], np.arange(0,99,1), self.sim_c[:99,1],
                         np.arange(0,99,1), self.sim_z[:99,1],'--')
                ax1.legend(['Savings', 'Consumption', 'Income'])  
                ax1.set_title('Simulation of First Household During First 100 Periods')
                
                #averages over entire simulation
                ax2.plot(np.arange(0,self.simT,1), np.mean(self.sim_sav, axis=1), 
                         np.arange(0,self.simT,1), np.mean(self.sim_c, axis=1) )
                ax2.legend(['Savings', 'Consumption', 'Income'])
                ax2.set_title('Simulation Average over 50,000 Households')
                #plt.savefig('simulation_egm_v1.pdf')
                plt.show()
            
            
            
            t4 = time.time()
            print(f'Plot time elapsed: {t4-t3:.2f} seconds')
            

        
            
            
        # e. print solution 
        
        if self.distribution_method != 'none':
            print("\n-----------------------------------------")
            print("Stationary Equilibrium Solution")
            print("-----------------------------------------")
            print(f"Steady State Assets = {self.a_ss:.2f}")
        
        if self.simulate or self.distribution_method == 'monte carlo' or self.full_euler_error:
            print("\n-----------------------------------------")
            print("Log10 Euler Equation Error Evaluation")
            print("-----------------------------------------")
            
            if self.full_euler_error:
                print(f"\nFull Grid Evalulation: Max Error  = {self.max_error:.2f}")
                print(f"Full Grid Evalulation: Average Error = {self.avg_error:.2f}")
        
            if self.simulate or self.distribution_method == 'monte carlo':
                print(f"\nSimulation: Max Error  = {self.max_error_sim:.2f}")
                print(f"Simulation: Average Error = {self.avg_error_sim:.2f}")
        
        
        t5 = time.time()
        print(f'\nTotal Run Time: {t5-t0:.2f} seconds')





################################
# II. JIT Compiled Functions   #
###############################


####################
# 1. Simulation   #
##################

@njit(parallel=True)
def simulate_MarkovChain(pol_cons, pol_sav, params_sim):
    """
    Simulates markov chain for T periods for N households and calculates the euler equation error for all 
    individuals at each point in time. In addition, it checks the grid size by issuing a warning if 1% of 
    households are at the maximum value (right edge) of the grid. The algorithm is from Ch.7 in Heer and Maussner.
    
    *Input
        - pol_cons: consumption policy function 
        - pol_sav: savings policy function 
        - params_sim: model parameters
    
    *Output
        - sim_c: consumption profile
        - sim_sav: savings (a') profile
        - sim_z: Income shock profile.
        - sim_m: cash-on-hand profile ((1+r)a + w*z)
        - euler_error_sim : error when the euler equation equality holds
    """
    
    # 1. initialize
    a0, z0, ret, w, simN, simT, grid_z, grid_sav, sigma, beta, pi, seed = params_sim
    
    np.random.seed(seed)
    sim_sav = np.zeros((simT,simN))
    sim_c = np.zeros((simT,simN))
    sim_m = np.zeros((simT,simN))
    sim_z = np.zeros((simT,simN))
    sim_z_idx = np.zeros((simT,simN), np.int32)
    euler_error_sim = np.empty((simT,simN)) * np.nan
    edge = 0
    
    
    
    # 2. helper functions
    
    # savings policy function interpolant
    polsav_interp = lambda a, z: interp(grid_sav, pol_sav[z, :], a)
    
    # marginal utility
    u_prime = lambda c : c**(-sigma)
    
    #inverse marginal utility
    u_prime_inv = lambda x : x ** (-1/sigma)
    
    
    
    # 3. simulate markov chain
    for t in range(simT):   #time

        draw = np.linspace(0, 1, simN)
        np.random.shuffle(draw)
        
        for i in prange(simN):  #individual

            # a. states 
            if t == 0:
                z_lag_idx = z0[i]
                a_lag = a0[i]
                
            else:
                z_lag_idx = sim_z_idx[t-1,i]
                a_lag = sim_sav[t-1,i]
                
            # b. shock realization. 0 for low state. 1 for high state.
            if draw[i] <= pi[z_lag_idx, 1]:     #state transition condition
            
                sim_z_idx[t,i] = 1      #index
                sim_z[t,i] = grid_z[sim_z_idx[t,i]]     #shock value
                
            else:
                sim_z_idx[t,i] = 0      #index
                sim_z[t,i] = grid_z[sim_z_idx[t,i]]     #shock value
                
            # c. income
            y = w*sim_z[t,i]
            
            # d. cash-on-hand path
            sim_m[t, i] = (1 + ret) * a_lag + y
            
            # e. savings path
            sim_sav[t,i] = polsav_interp(a_lag,sim_z_idx[t,i])
            if sim_sav[t,i] < grid_sav[0] : sim_sav[t,i] = grid_sav[0]     #ensure constraint binds
            
            # f. consumption path
            
            sim_c[t,i] = sim_m[t, i] - sim_sav[t,i]   
            
            
            
            # g. error evaluation
            
            check_out=False
            if sim_sav[t,i] >= pol_sav[sim_z_idx[t,i],-1]:
                edge = edge + 1
                check_out=True
                
            constrained=False
            if sim_sav[t,i] == grid_sav[0]:
                constrained=True
            
                
            if sim_c[t,i] < sim_m[t,i] and constrained==False and check_out==False :
                
                avg_marg_c_plus = 0
                
                for i_zz in range(len(grid_z)):      #next period productivity
                    
                    sav_int = polsav_interp(sim_sav[t,i],i_zz)
                    if sav_int < grid_sav[0] : sav_int = grid_sav[0]     #ensure constraint binds
                
                    c_plus = (1 + ret) * sim_sav[t,i] + w*grid_z[i_zz] - sav_int
                        
                    #expectation of marginal utility of consumption
                    avg_marg_c_plus += pi[sim_z_idx[t,i],i_zz] * u_prime(c_plus)
                
                #euler error
                euler_error_sim[t,i] = 1 - (u_prime_inv(beta*(1+ret)*avg_marg_c_plus) / sim_c[t,i])
            
            
    # 4. transform euler eerror to log_10 and get max and average
    euler_error_sim = np.log10(np.abs(euler_error_sim))
                
    # 5. grid size evaluation
    frac_outside = edge/grid_sav.size
    if frac_outside > 0.01 :
        raise Exception('Increase grid size!')
    
    return sim_c, sim_sav, sim_z, sim_m, euler_error_sim




###############################################################################
# 4. Stationary Distribution: Discrete Approximation and Forward Iteration   #
##############################################################################

@njit
def discrete_stationary_density_egm(pol_sav, a_star, params_discrete):
    """
    Discrete approximation of the density function adapted for the endogenous grid method. Approximates the 
    stationary joint density through forward iteration and linear interpolation over a discretized state space. 
    The algorithm is from Ch.7 in Heer and Maussner.
    
    *Input
        - pol_sav: savings policy function
        - params_discrete: model parameters
        
    *Output
        - stationary_pdf: joint stationary density function
        - it: number of iterations
    """
    
    # a. initialize
    
    grid_sav, Nz, pi, pi_stat, maxit, tol = params_discrete
    
    Ns = len(grid_sav)
    
    # initial guess uniform distribution
    stationary_pdf_old = np.ones((Ns, Nz))/Ns
    stationary_pdf_old = stationary_pdf_old * np.transpose(pi_stat)
    stationary_pdf_old = stationary_pdf_old.T
    
    # b. fixed point iteration
    for it in range(maxit):   # iteration 
        
        stationary_pdf = np.zeros((Nz, Ns))    # distribution in period t+1
             
        for i_z in range(Nz):     # iteration over productivity types in period t
            
            for i_s, s0 in enumerate(grid_sav):  # iteration over grid
            
                # i. interpolate
                s_intp = np.interp(s0, a_star[i_z,:], grid_sav)
                
                # ii. obtain distribution in period t+1   
                
                #less than or equal to lowest grid value
                if s_intp <= grid_sav[0]:
                    for i_zz in range(Nz):
                        stationary_pdf[i_zz,0] = stationary_pdf[i_zz,0] + stationary_pdf_old[i_z,i_s]*pi[i_z,i_zz]
                    
                #more than or equal to greatest grid value
                elif s_intp >= grid_sav[-1]:
                   for i_zz in range(Nz):
                        stationary_pdf[i_zz,-1] = stationary_pdf[i_zz,-1] + stationary_pdf_old[i_z,i_s]*pi[i_z,i_zz]
                   
                #inside grid
                else:
                   
                   j = np.sum(grid_sav <= s_intp) # grid index. s_intp lies between grid_sav[j-1] and grid_sav[j]
                   p0 = (s0 - a_star[i_z, j-1]) / (a_star[i_z, j] - a_star[i_z, j-1])
                
                   for i_zz in range(Nz):
                        
                            stationary_pdf[i_zz,j] = stationary_pdf[i_zz,j] + p0*stationary_pdf_old[i_z,i_s]*pi[i_z,i_zz]
                            stationary_pdf[i_zz,j-1] =stationary_pdf[i_zz,j-1] + (1-p0)*stationary_pdf_old[i_z,i_s]*pi[i_z,i_zz]
        
        
        #stationary distribution by percent 
        stationary_pdf=stationary_pdf/np.sum(np.sum(stationary_pdf,axis=0)) 
        
        # iii. calculate supremum norm
        dist = np.abs(stationary_pdf-stationary_pdf_old).max()
        
        if dist < tol:
            break
        
        else:
            stationary_pdf_old = np.copy(stationary_pdf)
        
    return stationary_pdf, it



#run everything

cs_EGM1=ConSaveEGMsmall()
cs_EGM1.solve_model()



