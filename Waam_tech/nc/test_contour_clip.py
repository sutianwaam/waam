import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))

from Waam_tech.nc.contour_clip import clip_paths_to_contours

paths = [((0, 0), (10, 0))]
outer = [(2, -1), (8, -1), (8, 1), (2, 1)]
holes = [[(4, -0.5), (6, -0.5), (6, 0.5), (4, 0.5)]]

clipped = clip_paths_to_contours(paths, outer, holes)
print("Clipped:", clipped)
# Expected: (2,0) to (4,0) and (6,0) to (8,0)
