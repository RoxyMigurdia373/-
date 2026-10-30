import math
import warnings
from shapely.geometry import Polygon, MultiPolygon, box

square = box(0, 0, 10, 10)
multipart_two = MultiPolygon([square, box(20, 0, 30, 10)])
poly_with_hole = Polygon(square.exterior, [box(2, 2, 4, 4).exterior])
hole_touching_exterior = Polygon(square.exterior, [[(0, 5), (2, 4), (2, 6), (0, 5)]])
two_holes_touch = Polygon(square.exterior, [box(2, 2, 4, 4).exterior, box(4, 2, 6, 4).exterior])
bowtie = Polygon([(0, 0), (10, 10), (0, 10), (10, 0), (0, 0)])
sharp_triangle = Polygon([(0, 0), (10, 0), (10 * math.cos(math.radians(20)), 10 * math.sin(math.radians(20)))])
square_with_dup_vertex = Polygon([(0, 0), (10, 0), (10, 0), (10, 10), (0, 10)])
tiny_edge = Polygon([(0, 0), (.001, 0), (10, 0), (10, 10), (0, 10)])
tiny_area = box(0, 0, 1, .5)
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    nan_coord = Polygon([(0, 0), (10, 0), (float("nan"), 10), (0, 10)])
empty = Polygon()
shared_edge_pair = [square, box(10, 0, 20, 10)]
overlap_pair = [square, box(9, 0, 19, 10)]
identical_pair = [square, square]
gap_three = [box(0, 0, 3, 10), box(7, 0, 10, 10), box(3, 0, 7, 3)]
sliver_pair = [square, box(9.9, 0, 19.9, 10)]
