"""Utility functions related to Bézier curves."""

from __future__ import annotations

from manim.typing import (
    BezierPoints,
    ColVector,
    MatrixMN,
    Point3D,
    Point3D_Array,
    PointDType,
    QuadraticBezierPoints,
    QuadraticBezierPoints_Array,
)

__all__ = [
    "bezier",
    "partial_bezier_points",
    "partial_quadratic_bezier_points",
    "interpolate",
    "integer_interpolate",
    "mid",
    "inverse_interpolate",
    "match_interpolate",
    "get_smooth_handle_points",
    "get_smooth_cubic_bezier_handle_points",
    "diag_to_matrix",
    "is_closed",
    "proportions_along_bezier_curve_for_point",
    "point_lies_on_bezier",
]


from collections.abc import Sequence
from functools import reduce
from typing import Any, Callable, overload

import numpy as np
import numpy.typing as npt
from scipy import linalg

from ..utils.simple_functions import choose
from ..utils.space_ops import cross2d, find_intersection


def bezier(
    points: Sequence[Point3D] | Point3D_Array,
) -> Callable[[float], Point3D]:
    """Classic implementation of a bezier curve.

    Parameters
    ----------
    points
        points defining the desired bezier curve.

    Returns
    -------
        function describing the bezier curve.
        You can pass a t value between 0 and 1 to get the corresponding point on the curve.
    """
    n = len(points) - 1
    # Cubic Bezier curve
    if n == 3:
        return lambda t: np.asarray(
            (1 - t) ** 3 * points[0]
            + 3 * t * (1 - t) ** 2 * points[1]
            + 3 * (1 - t) * t**2 * points[2]
            + t**3 * points[3],
            dtype=PointDType,
        )
    # Quadratic Bezier curve
    if n == 2:
        return lambda t: np.asarray(
            (1 - t) ** 2 * points[0] + 2 * t * (1 - t) * points[1] + t**2 * points[2],
            dtype=PointDType,
        )

    return lambda t: np.asarray(
        np.asarray(
            [
                (((1 - t) ** (n - k)) * (t**k) * choose(n, k) * point)
                for k, point in enumerate(points)
            ],
            dtype=PointDType,
        ).sum(axis=0)
    )


# !TODO: This function has still a weird implementation with the overlapping points
def partial_bezier_points(points: BezierPoints, a: float, b: float) -> BezierPoints:
    """Given an array of points which define bezier curve, and two numbers 0<=a<b<=1, return an array of the same size,
    which describes the portion of the original bezier curve on the interval [a, b].

    This algorithm is pretty nifty, and pretty dense.

    Parameters
    ----------
    points
        set of points defining the bezier curve.
    a
        lower bound of the desired partial bezier curve.
    b
        upper bound of the desired partial bezier curve.

    Returns
    -------
    np.ndarray
        Set of points defining the partial bezier curve.
    """
    _len = len(points)
    if a == 1:
        return np.asarray([points[-1]] * _len, dtype=PointDType)

    a_to_1 = np.asarray(
        [bezier(points[i:])(a) for i in range(_len)],
        dtype=PointDType,
    )
    end_prop = (b - a) / (1.0 - a)
    return np.asarray(
        [bezier(a_to_1[: i + 1])(end_prop) for i in range(_len)],
        dtype=PointDType,
    )


# Shortened version of partial_bezier_points just for quadratics,
# since this is called a fair amount
def partial_quadratic_bezier_points(
    points: QuadraticBezierPoints, a: float, b: float
) -> QuadraticBezierPoints:
    if a == 1:
        return np.asarray(3 * [points[-1]])

    def curve(t: float) -> Point3D:
        return np.asarray(
            points[0] * (1 - t) * (1 - t)
            + 2 * points[1] * t * (1 - t)
            + points[2] * t * t
        )

    # bezier(points)
    h0 = curve(a) if a > 0 else points[0]
    h2 = curve(b) if b < 1 else points[2]
    h1_prime = (1 - a) * points[1] + a * points[2]
    end_prop = (b - a) / (1.0 - a)
    h1 = (1 - end_prop) * h0 + end_prop * h1_prime
    return np.asarray((h0, h1, h2))


def split_quadratic_bezier(points: QuadraticBezierPoints, t: float) -> BezierPoints:
    """Split a quadratic Bézier curve at argument ``t`` into two quadratic curves.

    Parameters
    ----------
    points
        The control points of the bezier curve
        has shape ``[a1, h1, b1]``

    t
        The ``t``-value at which to split the Bézier curve

    Returns
    -------
        The two Bézier curves as a list of tuples,
        has the shape ``[a1, h1, b1], [a2, h2, b2]``
    """
    a1, h1, a2 = points
    s1 = interpolate(a1, h1, t)
    s2 = interpolate(h1, a2, t)
    p = interpolate(s1, s2, t)

    return np.array((a1, s1, p, p, s2, a2))


def subdivide_quadratic_bezier(points: QuadraticBezierPoints, n: int) -> BezierPoints:
    """Subdivide a quadratic Bézier curve into ``n`` subcurves which have the same shape.

    The points at which the curve is split are located at the
    arguments :math:`t = i/n` for :math:`i = 1, ..., n-1`.

    Parameters
    ----------
    points
        The control points of the Bézier curve in form ``[a1, h1, b1]``

    n
        The number of curves to subdivide the Bézier curve into

    Returns
    -------
        The new points for the Bézier curve in the form ``[a1, h1, b1, a2, h2, b2, ...]``

    .. image:: /_static/bezier_subdivision_example.png

    """
    beziers = np.empty((n, 3, 3))
    current = points
    for j in range(0, n):
        i = n - j
        tmp = split_quadratic_bezier(current, 1 / i)
        beziers[j] = tmp[:3]
        current = tmp[3:]
    return beziers.reshape(-1, 3)


def quadratic_bezier_remap(
    triplets: QuadraticBezierPoints_Array, new_number_of_curves: int
) -> QuadraticBezierPoints_Array:
    """Remaps the number of curves to a higher amount by splitting bezier curves

    Parameters
    ----------
    triplets
        The triplets of the quadratic bezier curves to be remapped shape(n, 3, 3)

    new_number_of_curves
        The number of curves that the output will contain. This needs to be higher than the current number.

    Returns
    -------
        The new triplets for the quadratic bezier curves.
    """
    difference = new_number_of_curves - len(triplets)
    if difference <= 0:
        return triplets
    new_triplets = np.zeros((new_number_of_curves, 3, 3))
    idx = 0
    for triplet in triplets:
        if difference > 0:
            tmp_noc = int(np.ceil(difference / len(triplets))) + 1
            tmp = subdivide_quadratic_bezier(triplet, tmp_noc).reshape(-1, 3, 3)
            for i in range(tmp_noc):
                new_triplets[idx + i] = tmp[i]
            difference -= tmp_noc - 1
            idx += tmp_noc
        else:
            new_triplets[idx] = triplet
            idx += 1
    return new_triplets

    """
    This is an alternate version of the function just for documentation purposes
    --------

    difference = new_number_of_curves - len(triplets)
    if difference <= 0:
        return triplets
    new_triplets = []
    for triplet in triplets:
        if difference > 0:
            tmp_noc = int(np.ceil(difference / len(triplets))) + 1
            tmp = subdivide_quadratic_bezier(triplet, tmp_noc).reshape(-1, 3, 3)
            for i in range(tmp_noc):
                new_triplets.append(tmp[i])
            difference -= tmp_noc - 1
        else:
            new_triplets.append(triplet)
    return new_triplets
    """


# Linear interpolation variants


@overload
def interpolate(start: float, end: float, alpha: float) -> float: ...


@overload
def interpolate(start: Point3D, end: Point3D, alpha: float) -> Point3D: ...


def interpolate(
    start: int | float | Point3D, end: int | float | Point3D, alpha: float | Point3D
) -> float | Point3D:
    return (1 - alpha) * start + alpha * end


def integer_interpolate(
    start: float,
    end: float,
    alpha: float,
) -> tuple[int, float]:
    """
    This is a variant of interpolate that returns an integer and the residual

    Parameters
    ----------
    start
        The start of the range
    end
        The end of the range
    alpha
        a float between 0 and 1.

    Returns
    -------
    tuple[int, float]
        This returns an integer between start and end (inclusive) representing
        appropriate interpolation between them, along with a
        "residue" representing a new proportion between the
        returned integer and the next one of the
        list.

    Example
    -------

    .. code-block:: pycon

        >>> integer, residue = integer_interpolate(start=0, end=10, alpha=0.46)
        >>> np.allclose((integer, residue), (4, 0.6))
        True
    """
    if alpha >= 1:
        return (int(end - 1), 1.0)
    if alpha <= 0:
        return (int(start), 0)
    value = int(interpolate(start, end, alpha))
    residue = ((end - start) * alpha) % 1
    return (value, residue)


@overload
def mid(start: float, end: float) -> float: ...


@overload
def mid(start: Point3D, end: Point3D) -> Point3D: ...


def mid(start: float | Point3D, end: float | Point3D) -> float | Point3D:
    """Returns the midpoint between two values.

    Parameters
    ----------
    start
        The first value
    end
        The second value

    Returns
    -------
        The midpoint between the two values
    """
    return (start + end) / 2.0


@overload
def inverse_interpolate(start: float, end: float, value: float) -> float: ...


@overload
def inverse_interpolate(start: float, end: float, value: Point3D) -> Point3D: ...


@overload
def inverse_interpolate(start: Point3D, end: Point3D, value: Point3D) -> Point3D: ...


def inverse_interpolate(
    start: float | Point3D, end: float | Point3D, value: float | Point3D
) -> float | Point3D:
    """Perform inverse interpolation to determine the alpha
    values that would produce the specified ``value``
    given the ``start`` and ``end`` values or points.

    Parameters
    ----------
    start
        The start value or point of the interpolation.
    end
        The end value or point of the interpolation.
    value
        The value or point for which the alpha value
        should be determined.

    Returns
    -------
        The alpha values producing the given input
        when interpolating between ``start`` and ``end``.

    Example
    -------

    .. code-block:: pycon

        >>> inverse_interpolate(start=2, end=6, value=4)
        0.5

        >>> start = np.array([1, 2, 1])
        >>> end = np.array([7, 8, 11])
        >>> value = np.array([4, 5, 5])
        >>> inverse_interpolate(start, end, value)
        array([0.5, 0.5, 0.4])
    """
    return np.true_divide(value - start, end - start)


@overload
def match_interpolate(
    new_start: float,
    new_end: float,
    old_start: float,
    old_end: float,
    old_value: float,
) -> float: ...


@overload
def match_interpolate(
    new_start: float,
    new_end: float,
    old_start: float,
    old_end: float,
    old_value: Point3D,
) -> Point3D: ...


def match_interpolate(
    new_start: float,
    new_end: float,
    old_start: float,
    old_end: float,
    old_value: float | Point3D,
) -> float | Point3D:
    """Interpolate a value from an old range to a new range.

    Parameters
    ----------
    new_start
        The start of the new range.
    new_end
        The end of the new range.
    old_start
        The start of the old range.
    old_end
        The end of the old range.
    old_value
        The value within the old range whose corresponding
        value in the new range (with the same alpha value)
        is desired.

    Returns
    -------
        The interpolated value within the new range.

    Examples
    --------
    >>> match_interpolate(0, 100, 10, 20, 15)
    50.0
    """
    old_alpha = inverse_interpolate(old_start, old_end, old_value)
    return interpolate(
        new_start,
        new_end,
        old_alpha,  # type: ignore
    )


def get_smooth_cubic_bezier_handle_points(
    points: Point3D_Array,
) -> tuple[BezierPoints, BezierPoints]:
    points = np.asarray(points)
    num_handles = len(points) - 1
    dim = points.shape[1]
    if num_handles < 1:
        return np.zeros((0, dim)), np.zeros((0, dim))
    # Must solve 2*num_handles equations to get the handles.
    # l and u are the number of lower an upper diagonal rows
    # in the matrix to solve.
    l, u = 2, 1
    # diag is a representation of the matrix in diagonal form
    # See https://www.particleincell.com/2012/bezier-splines/
    # for how to arrive at these equations
    diag: MatrixMN = np.zeros((l + u + 1, 2 * num_handles))
    diag[0, 1::2] = -1
    diag[0, 2::2] = 1
    diag[1, 0::2] = 2
    diag[1, 1::2] = 1
    diag[2, 1:-2:2] = -2
    diag[3, 0:-3:2] = 1
    # last
    diag[2, -2] = -1
    diag[1, -1] = 2
    # This is the b as in Ax = b, where we are solving for x,
    # and A is represented using diag.  However, think of entries
    # to x and b as being points in space, not numbers
    b: Point3D_Array = np.zeros((2 * num_handles, dim))
    b[1::2] = 2 * points[1:]
    b[0] = points[0]
    b[-1] = points[-1]

    def solve_func(b: ColVector) -> ColVector | MatrixMN:
        return linalg.solve_banded((l, u), diag, b)  # type: ignore

    use_closed_solve_function = is_closed(points)
    if use_closed_solve_function:
        # Get equations to relate first and last points
        matrix = diag_to_matrix((l, u), diag)
        # last row handles second derivative
        matrix[-1, [0, 1, -2, -1]] = [2, -1, 1, -2]
        # first row handles first derivative
        matrix[0, :] = np.zeros(matrix.shape[1])
        matrix[0, [0, -1]] = [1, 1]
        b[0] = 2 * points[0]
        b[-1] = np.zeros(dim)

        def closed_curve_solve_func(b: ColVector) -> ColVector | MatrixMN:
            return linalg.solve(matrix, b)  # type: ignore

    handle_pairs = np.zeros((2 * num_handles, dim))
    for i in range(dim):
        if use_closed_solve_function:
            handle_pairs[:, i] = closed_curve_solve_func(b[:, i])
        else:
            handle_pairs[:, i] = solve_func(b[:, i])
    return handle_pairs[0::2], handle_pairs[1::2]


def get_smooth_handle_points(
    points: BezierPoints,
) -> tuple[BezierPoints, BezierPoints]:
    """Given some anchors (points), compute handles so the resulting bezier curve is smooth.

    Parameters
    ----------
    points
        Anchors.

    Returns
    -------
    typing.Tuple[np.ndarray, np.ndarray]
        Computed handles.
    """
    # NOTE points here are anchors.
    points = np.asarray(points)
    num_handles = len(points) - 1
    dim = points.shape[1]
    if num_handles < 1:
        return np.zeros((0, dim)), np.zeros((0, dim))
    # Must solve 2*num_handles equations to get the handles.
    # l and u are the number of lower an upper diagonal rows
    # in the matrix to solve.
    l, u = 2, 1
    # diag is a representation of the matrix in diagonal form
    # See https://www.particleincell.com/2012/bezier-splines/
    # for how to arrive at these equations
    diag: MatrixMN = np.zeros((l + u + 1, 2 * num_handles))
    diag[0, 1::2] = -1
    diag[0, 2::2] = 1
    diag[1, 0::2] = 2
    diag[1, 1::2] = 1
    diag[2, 1:-2:2] = -2
    diag[3, 0:-3:2] = 1
    # last
    diag[2, -2] = -1
    diag[1, -1] = 2
    # This is the b as in Ax = b, where we are solving for x,
    # and A is represented using diag.  However, think of entries
    # to x and b as being points in space, not numbers
    b = np.zeros((2 * num_handles, dim))
    b[1::2] = 2 * points[1:]
    b[0] = points[0]
    b[-1] = points[-1]

    def solve_func(b: ColVector) -> ColVector | MatrixMN:
        return linalg.solve_banded((l, u), diag, b)  # type: ignore

    use_closed_solve_function = is_closed(points)
    if use_closed_solve_function:
        # Get equations to relate first and last points
        matrix = diag_to_matrix((l, u), diag)
        # last row handles second derivative
        matrix[-1, [0, 1, -2, -1]] = [2, -1, 1, -2]
        # first row handles first derivative
        matrix[0, :] = np.zeros(matrix.shape[1])
        matrix[0, [0, -1]] = [1, 1]
        b[0] = 2 * points[0]
        b[-1] = np.zeros(dim)

        def closed_curve_solve_func(b: ColVector) -> ColVector | MatrixMN:
            return linalg.solve(matrix, b)  # type: ignore

    handle_pairs = np.zeros((2 * num_handles, dim))
    for i in range(dim):
        if use_closed_solve_function:
            handle_pairs[:, i] = closed_curve_solve_func(b[:, i])
        else:
            handle_pairs[:, i] = solve_func(b[:, i])
    return handle_pairs[0::2], handle_pairs[1::2]


def diag_to_matrix(
    l_and_u: tuple[int, int], diag: npt.NDArray[Any]
) -> npt.NDArray[Any]:
    """
    Converts array whose rows represent diagonal
    entries of a matrix into the matrix itself.
    See scipy.linalg.solve_banded
    """
    l, u = l_and_u
    dim = diag.shape[1]
    matrix = np.zeros((dim, dim))
    for i in range(l + u + 1):
        np.fill_diagonal(
            matrix[max(0, i - u) :, max(0, u - i) :],
            diag[i, max(0, u - i) :],
        )
    return matrix


# Given 4 control points for a cubic bezier curve (or arrays of such)
# return control points for 2 quadratics (or 2n quadratics) approximating them.
def get_quadratic_approximation_of_cubic(
    a0: Point3D, h0: Point3D, h1: Point3D, a1: Point3D
) -> BezierPoints:
    a0 = np.array(a0, ndmin=2)
    h0 = np.array(h0, ndmin=2)
    h1 = np.array(h1, ndmin=2)
    a1 = np.array(a1, ndmin=2)
    # Tangent vectors at the start and end.
    T0 = h0 - a0
    T1 = a1 - h1

    # Search for inflection points.  If none are found, use the
    # midpoint as a cut point.
    # Based on http://www.caffeineowl.com/graphics/2d/vectorial/cubic-inflexion.html
    has_infl = np.ones(len(a0), dtype=bool)

    p = h0 - a0
    q = h1 - 2 * h0 + a0
    r = a1 - 3 * h1 + 3 * h0 - a0

    a = cross2d(q, r)
    b = cross2d(p, r)
    c = cross2d(p, q)

    disc = b * b - 4 * a * c
    has_infl &= disc > 0
    sqrt_disc = np.sqrt(np.abs(disc))
    settings = np.seterr(all="ignore")
    ti_bounds = []
    for sgn in [-1, +1]:
        ti = (-b + sgn * sqrt_disc) / (2 * a)
        ti[a == 0] = (-c / b)[a == 0]
        ti[(a == 0) & (b == 0)] = 0
        ti_bounds.append(ti)
    ti_min, ti_max = ti_bounds
    np.seterr(**settings)
    ti_min_in_range = has_infl & (0 < ti_min) & (ti_min < 1)
    ti_max_in_range = has_infl & (0 < ti_max) & (ti_max < 1)

    # Choose a value of t which starts at 0.5,
    # but is updated to one of the inflection points
    # if they lie between 0 and 1

    t_mid = 0.5 * np.ones(len(a0))
    t_mid[ti_min_in_range] = ti_min[ti_min_in_range]
    t_mid[ti_max_in_range] = ti_max[ti_max_in_range]

    m, n = a0.shape
    t_mid = t_mid.repeat(n).reshape((m, n))

    # Compute bezier point and tangent at the chosen value of t (these are vectorized)
    mid = bezier([a0, h0, h1, a1])(t_mid)  # type: ignore
    Tm = bezier([h0 - a0, h1 - h0, a1 - h1])(t_mid)  # type: ignore

    # Intersection between tangent lines at end points
    # and tangent in the middle
    i0 = find_intersection(a0, T0, mid, Tm)
    i1 = find_intersection(a1, T1, mid, Tm)

    m, n = np.shape(a0)
    result = np.zeros((6 * m, n))
    result[0::6] = a0
    result[1::6] = i0
    result[2::6] = mid
    result[3::6] = mid
    result[4::6] = i1
    result[5::6] = a1
    return result


def is_closed(points: Point3D_Array) -> bool:
    """Returns ``True`` if the spline given by ``points`` is closed, by
    checking if its first and last points are close to each other, or``False``
    otherwise.

    .. note::

        This function reimplements :meth:`np.allclose`, because repeated
        calling of :meth:`np.allclose` for only 2 points is inefficient.

    Parameters
    ----------
    points
        An array of points defining a spline.

    Returns
    -------
    :class:`bool`
        Whether the first and last points of the array are close enough or not
        to be considered the same, thus considering the defined spline as
        closed.

    Examples
    --------
    .. code-block:: pycon

        >>> import numpy as np
        >>> from manim import is_closed
        >>> is_closed(
        ...     np.array(
        ...         [
        ...             [0, 0, 0],
        ...             [1, 2, 3],
        ...             [3, 2, 1],
        ...             [0, 0, 0],
        ...         ]
        ...     )
        ... )
        True
        >>> is_closed(
        ...     np.array(
        ...         [
        ...             [0, 0, 0],
        ...             [1, 2, 3],
        ...             [3, 2, 1],
        ...             [1e-10, 1e-10, 1e-10],
        ...         ]
        ...     )
        ... )
        True
        >>> is_closed(
        ...     np.array(
        ...         [
        ...             [0, 0, 0],
        ...             [1, 2, 3],
        ...             [3, 2, 1],
        ...             [1e-2, 1e-2, 1e-2],
        ...         ]
        ...     )
        ... )
        False
    """
    start, end = points[0], points[-1]
    rtol = 1e-5
    atol = 1e-8
    tolerance = atol + rtol * start
    if abs(end[0] - start[0]) > tolerance[0]:
        return False
    if abs(end[1] - start[1]) > tolerance[1]:
        return False
    if abs(end[2] - start[2]) > tolerance[2]:
        return False
    return True


def proportions_along_bezier_curve_for_point(
    point: Point3D,
    control_points: BezierPoints,
    round_to: float = 1e-6,
) -> npt.NDArray[Any]:
    """Obtains the proportion along the bezier curve corresponding to a given point
    given the bezier curve's control points.

    The bezier polynomial is constructed using the coordinates of the given point
    as well as the bezier curve's control points. On solving the polynomial for each dimension,
    if there are roots common to every dimension, those roots give the proportion along the
    curve the point is at. If there are no real roots, the point does not lie on the curve.

    Parameters
    ----------
    point
        The Cartesian Coordinates of the point whose parameter
        should be obtained.
    control_points
        The Cartesian Coordinates of the ordered control
        points of the bezier curve on which the point may
        or may not lie.
    round_to
        A float whose number of decimal places all values
        such as coordinates of points will be rounded.

    Returns
    -------
        np.ndarray[float]
            List containing possible parameters (the proportions along the bezier curve)
            for the given point on the given bezier curve.
            This usually only contains one or zero elements, but if the
            point is, say, at the beginning/end of a closed loop, may return
            a list with more than 1 value, corresponding to the beginning and
            end etc. of the loop.

    Raises
    ------
    :class:`ValueError`
        When ``point`` and the control points have different shapes.
    """
    # Method taken from
    # http://polymathprogrammer.com/2012/04/03/does-point-lie-on-bezier-curve/

    if not all(np.shape(point) == np.shape(c_p) for c_p in control_points):
        raise ValueError(
            f"Point {point} and Control Points {control_points} have different shapes.",
        )

    control_points = np.array(control_points)
    n = len(control_points) - 1

    roots = []
    for dim, coord in enumerate(point):
        control_coords = control_points[:, dim]
        terms = []
        for term_power in range(n, -1, -1):
            outercoeff = choose(n, term_power)
            term = []
            sign = 1
            for subterm_num in range(term_power, -1, -1):
                innercoeff = choose(term_power, subterm_num) * sign
                subterm = innercoeff * control_coords[subterm_num]
                if term_power == 0:
                    subterm -= coord
                term.append(subterm)
                sign *= -1
            terms.append(outercoeff * sum(np.array(term)))
        if all(term == 0 for term in terms):
            # Then both Bezier curve and Point lie on the same plane.
            # Roots will be none, but in this specific instance, we don't need to consider that.
            continue
        bezier_polynom = np.polynomial.Polynomial(terms[::-1])
        polynom_roots = bezier_polynom.roots()  # type: ignore
        if len(polynom_roots) > 0:
            polynom_roots = np.around(polynom_roots, int(np.log10(1 / round_to)))
        roots.append(polynom_roots)

    roots = [[root for root in rootlist if root.imag == 0] for rootlist in roots]
    # Get common roots
    # arg-type: ignore
    roots = reduce(np.intersect1d, roots)  # type: ignore
    result = np.asarray([r.real for r in roots if 0 <= r.real <= 1])
    return result


def point_lies_on_bezier(
    point: Point3D,
    control_points: BezierPoints,
    round_to: float = 1e-6,
) -> bool:
    """Checks if a given point lies on the bezier curves with the given control points.

    This is done by solving the bezier polynomial with the point as the constant term; if
    any real roots exist, the point lies on the bezier curve.

    Parameters
    ----------
    point
        The Cartesian Coordinates of the point to check.
    control_points
        The Cartesian Coordinates of the ordered control
        points of the bezier curve on which the point may
        or may not lie.
    round_to
        A float whose number of decimal places all values
        such as coordinates of points will be rounded.

    Returns
    -------
    bool
        Whether the point lies on the curve.
    """

    roots = proportions_along_bezier_curve_for_point(point, control_points, round_to)

    return len(roots) > 0
