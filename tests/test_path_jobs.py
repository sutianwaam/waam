from __future__ import annotations

from types import SimpleNamespace

from Waam_tech.core.path_jobs import first_path_job, is_path_job, iter_path_jobs


def test_path_job_detected_by_proxy_type():
    obj = SimpleNamespace(Proxy=SimpleNamespace(Type="pathjob"))

    assert is_path_job(obj) is True


def test_path_job_detected_by_proxy_class_name():
    class CustomPathJobProxy:
        pass

    obj = SimpleNamespace(Proxy=CustomPathJobProxy())

    assert is_path_job(obj) is True


def test_path_job_detected_by_legacy_module_name():
    proxy_cls = type("Proxy", (), {"__module__": "PathScripts.PathJob"})
    obj = SimpleNamespace(Proxy=proxy_cls())

    assert is_path_job(obj) is True


def test_path_feature_type_id_requires_job_specific_shape():
    path_job = SimpleNamespace(TypeId="Path::FeaturePython", SetupSheet=object())
    path_operation = SimpleNamespace(TypeId="Path::FeaturePython", Proxy=object())

    assert is_path_job(path_job) is True
    assert is_path_job(path_operation) is False


def test_iter_path_jobs_and_first_path_job_share_detection_order():
    op = SimpleNamespace(TypeId="Path::FeaturePython", Proxy=object())
    job1 = SimpleNamespace(Name="Job1", Proxy=SimpleNamespace(Type="PathJob"))
    job2 = SimpleNamespace(Name="Job2", TypeId="Path::FeaturePython", SetupSheet=object())
    doc = SimpleNamespace(Objects=[op, job1, job2])

    assert list(iter_path_jobs(doc)) == [job1, job2]
    assert first_path_job(doc) is job1
