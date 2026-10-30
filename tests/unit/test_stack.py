from __future__ import annotations

from nexo.kernel.quality.stack import detect_stacks


def read_from(files: dict[str, str]):  # type: ignore[no-untyped-def]
    return lambda rel: files.get(rel, "")


def test_detects_multiple_stacks() -> None:
    files = {
        "api/Api.csproj": "",
        "web/package.json": '{"dependencies": {"react": "18"}}',
        "tools/pyproject.toml": "",
    }
    assert detect_stacks(files, read_from(files)) == ["dotnet", "react", "python"]


def test_node_without_react_and_invalid_json() -> None:
    assert detect_stacks(["package.json"], read_from({"package.json": "{}"})) == ["node"]
    assert detect_stacks(["package.json"], read_from({"package.json": "not json"})) == ["node"]


def test_ignores_node_modules() -> None:
    files = {"node_modules/x/package.json": '{"dependencies":{"react":"1"}}'}
    assert detect_stacks(files, read_from(files)) == []


PUBSPEC = "name: app\ndependencies:\n  flutter:\n    sdk: flutter\nflutter:\n  uses-material-design: true\n"


def test_flutter_app_is_not_java_because_of_its_android_folder() -> None:
    files = {
        "pubspec.yaml": PUBSPEC,
        "android/build.gradle.kts": "",
        "android/app/build.gradle.kts": "",
        "android/settings.gradle.kts": "",
    }
    assert detect_stacks(files, read_from(files)) == ["flutter"]


def test_java_angular_flutter_side_by_side() -> None:
    files = {
        "backend/pom.xml": "<project/>",
        "batch/build.gradle": "",
        "web/package.json": '{"dependencies": {"@angular/core": "^20.0.0", "rxjs": "7"}}',
        "app/pubspec.yaml": PUBSPEC,
        "app/android/build.gradle": "",
        "tool/pubspec.yaml": "name: tool\ndependencies:\n  args: ^2.0.0\n",  # Dart puro: fora
    }
    assert detect_stacks(files, read_from(files)) == ["java", "angular", "flutter"]
