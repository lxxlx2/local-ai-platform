import importlib.util
from pathlib import Path


SCRIPT=Path(__file__).parents[1]/"scripts/presentation-video.py"
spec=importlib.util.spec_from_file_location("presentation_video_cli",SCRIPT)
cli=importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)


def test_owner_cli_has_required_commands_and_safe_defaults():
    parser=cli.parser()
    args=parser.parse_args(["presentation","build","--input","deck.pptx"])
    assert args.narration=="hybrid"
    assert args.language=="auto"
    assert args.voice_profile=="auto"
    assert args.mixed_language_mode=="dominant"
    for argv in (["voice","status"],["voice","create-defaults"],["voice","inspect","x"],
                 ["voice","qualify","x"],["presentation","inspect","--input","x.pptx"],
                 ["presentation","resume","--job-id","presentation-1234"],
                 ["presentation","status","--job-id","presentation-1234"]):
        assert parser.parse_args(argv)


def test_wrapper_uses_fixed_control_plane_interpreter_and_no_shell_eval():
    wrapper=(SCRIPT.with_suffix(".sh")).read_text()
    assert "/Users/jerson/AI/runtime/control-plane-venv/bin/python" in wrapper
    assert "eval" not in wrapper and "bash -c" not in wrapper
