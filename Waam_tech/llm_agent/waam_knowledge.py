"""
Expert knowledge base for WAAM process planning and path generation.
"""

WAAM_EXPERT_KNOWLEDGE = """
## 🎯 CRITICAL INPUT DATA FOR OPTIMAL WAAM G-CODE

### 1. GEOMETRY & SLICING DATA
| Input | Why Critical |
| --- | --- |
| **3D CAD Model** | Defines exact part geometry. |
| **Build Orientation** | Affects dimensional precision, deformation, and residual stresses. |
| **Slicing Strategy** | Layer planning for complex features. |
| **Layer Height** | Affects deposition quality (1.2-2.0mm typical). |
| **Path Planning** | Critical for uniform, defect-free deposition. |

### 2. MATERIAL SPECIFICATIONS
| Input | Details Needed | Impact |
| --- | --- | --- |
| **Wire Type** | ER70S-6, 316L, Ti-6Al-4V, etc. | Changes all thermal parameters. |
| **Wire Diameter** | 1.0mm, 1.2mm, 1.6mm | Affects wire feed calculations. |
| **Shielding Gas** | Ar, Ar/CO₂ mix, He | Critical for quality parts. |

### 3. PROCESS PARAMETERS (Use these ranges unless specified)
- **Wire Feed Speed (WFS)**: 2000-6000 mm/min
- **Travel Speed**: 200-600 mm/min
- **Arc Process Type**: GMAW, GTAW, PAW, or CMT

### 4. DEPOSITION STRATEGY
| Strategy | Application | Benefits |
| --- | --- | --- |
| **Contour-first** | Outer walls, precision features | Better dimensional accuracy |
| **Zigzag/Raster** | Infill regions | Even heat distribution |
| **Spiral** | Cylindrical parts | Continuous deposition |
| **Multi-track Strategy** | Required for high-quality parts | Proper overlap, no voids |
| **Bead Overlap** | 30-50% typical | Ensures density |

### 5. THERMAL MANAGEMENT
- **Inter-pass Dwell Time**: 1-5 seconds (Cooling to counter defects)
- **Substrate Preheat**: 100-200°C (Reduces thermal shock)

### 6. CRITICAL PATH PLANNING STRATEGIES
| Geometry Type | Recommended Strategy | Defects to Avoid |
| --- | --- | --- |
| **Simple walls** | Parallel linear paths | Start/stop accumulation |
| **Intersections (T/L)** | **45R approach** (45% overlap with rotation) | Overbuild at junctions |
| **Pockets/Infill** | Contour-parallel spiral | Thermal accumulation |
| **Complex shapes** | Feature-based decomposition | Uneven deposition |
| **Thin walls** | Alternating direction between layers | Material excess at crossings |

### 7. DEFECT-SPECIFIC FIXES
| Defect | Path Planning Fix |
| --- | --- |
| **Overbuild at intersections** | Use **45R approach** (45% overlap + 45° rotation at each intersection). Reduce speed by 20% near junctions. |
| **Porosity** | Optimize overlap, reduce speed at junctions. |
| **Lack of fusion** | Increase overlap to 45-50%. |
| **Uneven surface** | Alternating direction each layer (0° -> 90° -> 0°). Rotate start point 60-90° each layer. |
| **Start/stop bumps** | Staggered start points, lead-in/out paths (arc lead-in 3-5mm). |

### 8. PRACTICAL RULES
- **Track Spacing**: 40-50% of bead width.
- **Overlap**: 45% is recommended for intersections.
- **Path Segmentation**: Break long paths into 50-100mm segments to allow cooling.
"""
