import importlib

from server import cli


def test_every_command_module_registers():
    for name in cli.COMMANDS:
        module = importlib.import_module(f"server.cli.{name}")
        assert callable(module.register), name


def test_help_lists_commands(capsys):
    parser = cli.build_parser()
    help_text = parser.format_help()
    for name in cli.COMMANDS:
        assert name in help_text
