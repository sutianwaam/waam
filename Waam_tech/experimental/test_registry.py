from Waam_tech.experimental.registry import select_experimental_planner


def test_auto_selection_uses_runtime_active_planner() -> None:
    planner = select_experimental_planner()
    assert planner is not None
    assert planner.key == "runtime_active"
