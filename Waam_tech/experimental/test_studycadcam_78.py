from Waam_tech.experimental.studycadcam_78 import StudyCadCam78ExperimentalPlanner


def test_projected_cross_axis_holes_accept_extracted_hole_types() -> None:
    planner = StudyCadCam78ExperimentalPlanner()
    outer = [(0.0, 0.0), (20.0, 0.0), (20.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    features = {
        "features": [
            {
                "type": "through_hole",
                "axis_wcs": [1.0, 0.0, 0.0],
                "center": [5.0, 5.0, 0.0],
                "diameter": 2.0,
            },
            {
                "type": "cylindrical_hole",
                "axis_wcs": [0.0, 1.0, 0.0],
                "center": [14.0, 5.0, 0.0],
                "diameter": 3.0,
            },
        ]
    }

    projected = planner._build_projected_cross_axis_holes(features, outer, direct_holes=[])

    assert len(projected) == 2
