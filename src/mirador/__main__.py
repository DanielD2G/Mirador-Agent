from mirador.adapters.cli.app import app, configure
from mirador.bootstrap import build_app


def main() -> None:
    configure(build_app)
    app()


if __name__ == "__main__":
    main()
