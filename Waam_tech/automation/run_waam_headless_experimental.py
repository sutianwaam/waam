try:
    from .headless_pipeline import main
except ImportError:
    from headless_pipeline import main


if __name__ == "__main__":
    main(enable_experimental=True)
