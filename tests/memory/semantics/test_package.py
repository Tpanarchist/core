"""Propositions for the memory package's own scaffolding."""

from _memory_side_effects import assert_fresh_import_has_no_side_effects


class TestPackageImport:
    def test_memory_package_imports(self) -> None:
        import memory

        assert memory.__version__ == "0.1.0"

    def test_memory_import_has_no_side_effects(self) -> None:
        assert_fresh_import_has_no_side_effects(
            "memory",
            patch_targets=(
                "uuid.uuid4",
                "time.time",
                "time.monotonic",
            ),
        )
