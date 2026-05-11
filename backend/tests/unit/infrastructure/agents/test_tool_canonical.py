from src.infrastructure.agents.tool_canonical import canonicalize_tool


class TestBash:
    def test_claude_code_shape_preserved(self) -> None:
        name, args = canonicalize_tool(
            "Bash", {"command": "ls -la", "description": "list files"}
        )
        assert name == "Bash"
        assert args == {"command": "ls -la", "description": "list files"}

    def test_amp_cmd_renamed_to_command(self) -> None:
        name, args = canonicalize_tool("Bash", {"cmd": "git status", "cwd": "/tmp"})
        assert name == "Bash"
        assert args == {"command": "git status", "cwd": "/tmp"}

    def test_optional_fields_passed_through(self) -> None:
        name, args = canonicalize_tool(
            "Bash",
            {"command": "sleep 1", "run_in_background": True, "timeout": 5000},
        )
        assert name == "Bash"
        assert args == {
            "command": "sleep 1",
            "run_in_background": True,
            "timeout": 5000,
        }


class TestEdit:
    def test_claude_code_keys_renamed(self) -> None:
        name, args = canonicalize_tool(
            "Edit",
            {
                "file_path": "/x/y.py",
                "old_string": "foo",
                "new_string": "bar",
                "replace_all": True,
            },
        )
        assert name == "Edit"
        assert args == {
            "path": "/x/y.py",
            "old_text": "foo",
            "new_text": "bar",
            "replace_all": True,
        }

    def test_amp_edit_file_renamed_with_keys(self) -> None:
        name, args = canonicalize_tool(
            "edit_file",
            {"path": "/a.py", "old_str": "x", "new_str": "y"},
        )
        assert name == "Edit"
        assert args == {"path": "/a.py", "old_text": "x", "new_text": "y"}

    def test_empty_old_string_preserved(self) -> None:
        # New-file insertion: old_string="" is meaningful and must NOT
        # fall through to "missing" behaviour.
        name, args = canonicalize_tool(
            "Edit",
            {"file_path": "/new.py", "old_string": "", "new_string": "hello"},
        )
        assert name == "Edit"
        assert args == {"path": "/new.py", "old_text": "", "new_text": "hello"}


class TestMultiEdit:
    def test_edits_recursively_canonicalized(self) -> None:
        name, args = canonicalize_tool(
            "MultiEdit",
            {
                "file_path": "/x.py",
                "edits": [
                    {"old_string": "a", "new_string": "b"},
                    {"old_string": "c", "new_string": "d", "replace_all": True},
                ],
            },
        )
        assert name == "MultiEdit"
        assert args == {
            "path": "/x.py",
            "edits": [
                {"old_text": "a", "new_text": "b"},
                {"old_text": "c", "new_text": "d", "replace_all": True},
            ],
        }


class TestRead:
    def test_amp_read_range_renamed(self) -> None:
        name, args = canonicalize_tool(
            "Read", {"path": "/x.py", "read_range": "1-100"}
        )
        assert name == "Read"
        assert args == {"path": "/x.py", "line_range": "1-100"}

    def test_claude_code_offset_limit_derives_line_range(self) -> None:
        name, args = canonicalize_tool(
            "Read", {"file_path": "/x.py", "offset": 10, "limit": 50}
        )
        assert name == "Read"
        assert args == {"path": "/x.py", "line_range": "10-59"}

    def test_offset_only_yields_open_ended_range(self) -> None:
        _, args = canonicalize_tool("Read", {"file_path": "/x.py", "offset": 100})
        assert args == {"path": "/x.py", "line_range": "100+"}

    def test_no_range_when_neither_offset_nor_limit(self) -> None:
        _, args = canonicalize_tool("Read", {"file_path": "/x.py"})
        assert args == {"path": "/x.py"}


class TestWrite:
    def test_claude_code_shape_renamed(self) -> None:
        name, args = canonicalize_tool(
            "Write", {"file_path": "/x.py", "content": "hello"}
        )
        assert name == "Write"
        assert args == {"path": "/x.py", "content": "hello"}

    def test_amp_create_file_renamed(self) -> None:
        name, args = canonicalize_tool(
            "create_file", {"path": "/x.py", "contents": "hello"}
        )
        assert name == "Write"
        assert args == {"path": "/x.py", "content": "hello"}


class TestGrep:
    def test_amp_finder_renamed(self) -> None:
        name, args = canonicalize_tool("finder", {"query": "TODO"})
        assert name == "Grep"
        assert args == {"pattern": "TODO"}

    def test_claude_code_passthrough(self) -> None:
        name, args = canonicalize_tool(
            "Grep", {"pattern": "TODO", "path": "/src"}
        )
        assert name == "Grep"
        assert args == {"pattern": "TODO", "path": "/src"}


class TestUnknownTool:
    def test_unknown_passes_through_unchanged(self) -> None:
        name, args = canonicalize_tool(
            "WeirdCustomTool", {"foo": 1, "bar": [2, 3]}
        )
        assert name == "WeirdCustomTool"
        assert args == {"foo": 1, "bar": [2, 3]}


class TestIdempotence:
    def test_already_canonical_input_is_unchanged(self) -> None:
        canonical = {"path": "/x.py", "old_text": "a", "new_text": "b"}
        name, args = canonicalize_tool("Edit", dict(canonical))
        assert name == "Edit"
        assert args == canonical
