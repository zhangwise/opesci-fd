from sympy import Indexed, IndexedBase, solve, Eq
from util import *
from derivative import *

__all__ = ['SField', 'VField', 'Media', 'RegularField']


class Field(IndexedBase):
    """
    - Class to represent fields on staggered grid
    - Extends sympy IndexedBase class, can be indexed with [] operator
    - Parent class to VFeild (velocity) and SField (stress)
    - Holds relevant information such as dimension, staggered-ness,
    expression for FD approximation of derivatives,
    code to calculate boundary cells
    """

    def __new__(typ, name, **kwargs):
        obj = IndexedBase.__new__(typ, name)
        return obj

    def __init__(self, *args, **kwargs):
        super(Field, self).__init__()

        # Pass additional arguments to self.set()
        # Sympy.solve seems to call the constructor a second time with no parameters.
        # This condition here prevents calling set a second time (with no parameters) and failing
        if len(kwargs) > 1:
            self.set(**kwargs)

    def set(self, dimension, staggered):
        self.dimension = dimension
        self.staggered = staggered

        # list of list to store boundary ghost cell code
        self.bc = [[None]*2 for x in range(dimension+1)]

    def set_analytic_solution(self, function):
        """
        set analytical function (exact solution) of the field
        used to compare against numerical solution
        param function: expression of the analytical function
        """
        self.sol = function

    def set_indices(self, indices):
        """
        set the list of index symbols, e.g [t,x,y,z]
        """
        self.indices = indices

    def set_spacing(self, spacing):
        """
        set the list of grid spacing symbols, e.g [dt,dx,dy,dz]
        """
        self.spacing = spacing

    def set_order(self, order):
        """
        set order of accuracy of the field, e.g. [1,2,2,2] for (2,4) scheme
        """
        self.order = order

    def calc_derivative(self, l, k, d, n, order_of_derivative):
        """
        return FD approximations field derivatives
        input param description same as Deriv_half()
        """
        return Deriv_half(self, l, k, d, n/2)[order_of_derivative]

    def populate_derivatives(self, max_order=1):
        """
        iterate through dimensions to populate the Field.d with Derivative objects
        Field.d is list of list to store derivative expressions
        first index is for dimension, 2nd index is for order of derivative
        e.g. self.d[0][1] = dF/dt, self.d[2][2] = dF2/d2y
        create the FD approximates and assign to the Derivative objects
        """
        self.d = [[None]*(max_order+1) for x in range(self.dimension+1)]
        for d in range(self.dimension+1):
            # iterate through all indices [t,x,y,z]

            index = self.indices[d]
            # This loop might not be required. Every time this loop is called, it inverts the matrix and finds all derivatives
            # though it just picks up one element from the resulting vector and repeats the entire operation
            for order in range(1, max_order+1):
                # iterate through all orders of derivatives
                # create DDerivative objects (name, dependent variable, derivative order, max_accuracy needed)
                # name = 'D'+'_'+self.label.name+'_'+str(index)+'_'+str(order)  # e.g. D_U_x_1 = dU/dx
                name = ''.join(['\partial ', self.label.name, '/\partial ', str(index)])

                self.d[d][order] = DDerivative(name, index, order, self.order[d])

                for accuracy in range(2, self.order[d]+2, 2):
                    # assign FD approximation expression of different order of accuracy
                    self.d[d][order].fd[accuracy] = self.calc_derivative(self.indices, d, self.spacing[d], accuracy, order)

    def align(self, expr):
        """
        - shift the indices of fields in input expression
        according to the staggered-ness of this field
        - used to convert relative offset reference between fields
        to absolute reference (prepare to be converted to array)
        - return the modified expression
        """
        if expr.is_Symbol or expr.is_Number:
            return expr
        if isinstance(expr, Indexed):
            b = expr.base
            if not (isinstance(b, VField) or isinstance(b, SField) or isinstance(b, Media)):
                return expr
            if isinstance(b, Media):
                idx = list(expr.indices)
                for d in range(self.dimension):
                    if self.staggered[d+1]:
                        idx[d] += hf
                return b[idx]
            # align indices if input field staggered different from this field
            idx = []
            for k in range(len(expr.indices)):
                # if both are staggered or unstaggered in direction k
                # index is unchanged
                if self.staggered[k] == b.staggered[k]:
                    idx += [expr.indices[k]]
                # if this field is staggered but target field is unstaggered
                # in direction k, shift by +1/2
                elif self.staggered[k]:
                    idx += [expr.indices[k]+hf]
                # if this field is unstaggered but target field is staggered
                # in direction k, shift by -1/2
                else:
                    idx += [expr.indices[k]-hf]
            tmp = b[idx]
            return tmp
        # recursive call for all arguments of expr
        args = tuple([self.align(arg) for arg in expr.args])
        result = expr.func(*args)
        return result

    def set_fd_kernel(self, kernel):
        """
        set the updating kernel of this field
        e.g. the expression to calculate U[t+1,x,y,z]
        store the kernel to self.fd
        store the aligned expression to self.fd_align
        """
        self.kernel = kernel
        tmp = self.align(kernel)
        self.kernel_aligned = tmp

    def set_dt(self, dt):
        """
        set the expression of first time derivative of the field, e.g. dU/dt
        used to calculate ghost cells for free-surface boundary condition
        """
        self.dt = dt

    def associate_stress_fields(self, sfields):
        """
        link this velocity field to a list of stress field
        e.g. Vx will be associated with Txx, Txy, Txz
        link compression stress field with other compression stress field
        e.g. Txx with Txx, Tyy, Tzz
        :param sfields: list of associated stress field
        """
        self.sfields = sfields


class VField(Field):
    """
    Class to represent velocity field on staggered grid
    subclass of Field
    """

    def set(self, dimension, direction):
        """
        - set number of dimensions and direction of the velocity field
        - work out the staggered-ness according to the direction
        - a velocity field is only staggered in the spatial index
        same as its direction
        - a velocity field is always staggered in time index
        i.e. in 3D, field Vx will have staggered = [True, True, False, False]
        :param dimension: number of dimensions, e.g. 3
        :param direction: the direction of the field, e.g. 1
        """
        self.direction = direction
        staggered = [False] * (dimension+1)
        staggered[0] = True
        staggered[direction] = True
        Field.set(self, dimension, staggered)

    def set_free_surface(self, d, b, side, algo='robertsson'):
        """
        - set free surface boundary condition to boundary d, at index b
        :param d: direction of the boundary surface normal
        :param b: location of the boundary (index)
        :param side: lower boundary (0) or upper boundary (1)
        :param algo: which algorithm to use to compute ghost cells
        algo == 'robertsson' [1]: setting all velocities at ghost cells to zero
        algo == 'levander' [2]: only valid for 4th spatial order. using 2nd order FD approximation for velocities
        - e.g. set_free_surface([t,x,y,z],1,2,0)
        set y-z plane at x=2 to be lower free surface
        - ghost cells are calculated using reflection of stress fields
        - store the symbolic equations to populate ghost cells in self.bc
        [1] Robertsson, Johan OA. "A numerical free-surface condition for elastic/viscoelastic finite-difference modeling in the presence of topography." Geophysics 61.6 (1996): 1921-1934.
        [2] Levander, Alan R. "Fourth-order finite-difference P-SV seismograms." Geophysics 53.11 (1988): 1425-1436.
        """
        if algo == 'levander':
            # use this stress field to solve for ghost cell expression
            field = self.sfields[d]
            expr = field.dt
            idx = list(self.indices)
            # create substituion dictionary
            dict1 = {}
            derivatives = get_all_objects(expr, DDerivative)
            for deriv in derivatives:
                # using 2nd order approximation
                dict1[deriv] = deriv.fd[2]
            expr = expr.subs(dict1)
            if self.staggered[d]:
                # if staggered, solve ghost cell using T'[b]=0 (e.g. W at z surface, using Tzz)
                eq = Eq(expr)
                shift = hf
                t = b - hf  # real boundary location
            else:
                # if not staggered, solve ghost cell using T'[b-1/2]=T'[b+1/2] (e.g. U at z surface, using Txz)
                eq = Eq(expr.subs(idx[d], idx[d]-hf),
                        expr.subs(idx[d], idx[d]+hf))
                shift = 1
                t = b

            idx[d] -= ((-1)**side)*shift
            lhs = self[idx]
            rhs = solve(eq, lhs)[0]
            lhs = lhs.subs(self.indices[d], t)
            rhs = self.align(rhs.subs(self.indices[d], t))

            # change ti to t+1
            lhs = lhs.subs(idx[0], idx[0]+1)
            rhs = rhs.subs(idx[0], idx[0]+1)

            self.bc[d][side] = [Eq(lhs, rhs)]
        elif algo == 'robertsson':
            idx = list(self.indices)
            if self.staggered[d]:
                # e.g. W at z boundary
                idx[d] = b - (1-side)
            else:
                # e.g. U at z boundary
                idx[d] = b - (-1)**side
            eq = Eq(self[idx])
            eq = eq.subs(idx[0], idx[0]+1)
            self.bc[d][side] = [eq]
            # populate all ghost cells
            for depth in range(self.order[d]/2-1):
                idx[d] -= (-1)**side
                eq = Eq(self[idx])
                eq = eq.subs(idx[0], idx[0]+1)
                self.bc[d][side].append(eq)
        else:
            raise ValueError('Unknown boundary condition algorithm')


class SField(Field):
    """
    Class to represent stress fields on staggered grid
    subclass of Field
    """

    def set(self, dimension, direction):
        """
        - set number of dimensions and direction of the stress field
        - work out the staggered-ness according to the direction
        - compression stress fields are not staggered
        - sheer stress fields are staggered in the surface
        normal and force direction
        - stress fields are not staggered in time index
        i.e. in 3D, field Txx has staggered = [False, False, False, False]
        Txy has staggered = [False, True, True, False]
        :param dimension: number of dimensions, e.g. 3
        :param direction: the direction of the field, e.g. (1,1) for Txx
        """
        self.direction = direction
        staggered = [False] * (dimension+1)
        if direction[0] == direction[1]:
            # compression stress, not staggered
            Field.set(self, dimension, staggered)
        else:
            # sheer stress, staggered
            for i in range(len(direction)):
                staggered[direction[i]] = True
            Field.set(self, dimension, staggered)

    def set_free_surface(self, d, b, side, algo='robertsson'):
        """
        set free surface boundary condition to boundary d, at index b
        :param indices: list of indices, e.g. [t,x,y,z] for 3D
        :param d: direction of the boundary surface normal
        :param b: location of the boundary (index)
        :param algo: which algorithm to use to compute ghost cells
        algo == 'robertsson' [1]: setting all velocities at ghost cells to zero
        algo == 'levander' [2]: only valid for 4th spatial order. using 2nd order FD approximation for velocities
        side: lower boundary (0) or upper boundary (1)
        e.g. set_free_surface([t,x,y,z],1,2,0)
        set y-z plane at x=2 to be lower free surface
        ghost cells are calculated using reflection of stress fields
        store the code to populate ghost cells to self.bc
        [1] Robertsson, Johan OA. "A numerical free-surface condition for elastic/viscoelastic finite-difference modeling in the presence of topography." Geophysics 61.6 (1996): 1921-1934.
        [2] Levander, Alan R. "Fourth-order finite-difference P-SV seismograms." Geophysics 53.11 (1988): 1425-1436.
        """
        idx = list(self.indices)

        if d not in self.direction:
            if (not algo == 'levander') or (not self.direction[0] == self.direction[1]):
                # shear stress, e.g. Tyz no need to recalculate at x boundary (only depends on dV/dz and dW/dy)
                self.bc[d][side] = []
                return
            else:
                # normal stress, need to recalcuate Tyy, Tzz at x boundary
                expr = self.dt
                derivatives = get_all_objects(expr, DDerivative)
                for deriv in derivatives:
                    if deriv.var == idx[d]:
                        # replacing dx at x boundary with dy, dz terms
                        expr2 = self.sfields[d].dt
                        deriv_0 = deriv
                        deriv_sub = solve(expr2, deriv)[0]
                        break
                expr = expr.subs(deriv_0, deriv_sub)
                derivatives = get_all_objects(expr, DDerivative)
                # substitution dictionary
                dict1 = {}
                for deriv in derivatives:
                    dict1[deriv] = deriv.fd[4]
                expr = expr.subs(dict1)
                eq = Eq(self.d[0][1].fd[2], expr)
                eq = eq.subs(idx[d], b)
                t = idx[0]
                idx[0] = t+hf
                idx[d] = b
                # eq = eq.subs(t, t+hf)
                # idx[0] = t+1
                # idx[d] = b
                # solve for Txx(t+1/2)
                lhs = self[idx]
                rhs = solve(eq, lhs)[0]
                rhs = self.align(rhs)
                # change t+1/2 to t+1
                lhs = lhs.subs(t, t+hf)
                rhs = rhs.subs(t, t+hf)
                eq2 = Eq(lhs, rhs)
                self.bc[d][side] = [eq2]
                return

        # use anti-symmetry to ensure stress at boundary=0
        # this applies for all algorithms

        idx = list(self.indices)  # ghost cell
        idx2 = list(self.indices)  # cell inside domain

        if not self.staggered[d]:
            # if not staggered, assign T[d]=0, assign T[d-1]=-T[d+1]
            idx[d] = b
            idx2[d] = b
            eq1 = Eq(self[idx])
        else:
            # if staggered, assign T[d-1/2]=T[d+1/2], assign T[d-3/2]=T[d+3/2]
            idx[d] = b - (1-side)
            idx2[d] = idx[d] + (-1)**side
            eq1 = Eq(self[idx], -self[idx2])
        eq1 = eq1.subs(idx[0], idx[0]+1)
        self.bc[d][side] = [eq1]

        for depth in range(self.order[d]/2-1):
            # populate ghost cells
            idx[d] -= (-1)**side
            idx2[d] += (-1)**side
            eq = Eq(self[idx], -self[idx2])
            # change t to t+1
            eq = eq.subs(idx[0], idx[0]+1)
            self.bc[d][side].append(eq)


class Media(IndexedBase):
    """
    Class to represent media parameters, e.g. rho, vp, vs, lambda, mu (plus effective media parameters)
    """

    def __new__(typ, name, **kwargs):
        obj = IndexedBase.__new__(typ, name)
        return obj

    def __init__(self, *args, **kwargs):
        super(Media, self).__init__()

        # Pass additional arguments to self.set()
        if len(kwargs) > 0:
            self.set(**kwargs)

    def set(self, dimension, staggered, index):
        """
        set the dimension, staggered-ness and indices of the media parameter
        """
        self.dimension = dimension
        self.staggered = staggered
        self.index = index


class RegularField(Field):
    def __init__(self, *args, **kwargs):
        super(RegularField, self).__init__(staggered=[0, 0, 0], *args, **kwargs)

    def calc_derivative(self, l, k, d, n, order_of_derivative):
        """
        return FD approximations field derivatives
        input param description same as Deriv_half()
        """
        full = Deriv(self, l, k, d, n)[order_of_derivative]
        return full
