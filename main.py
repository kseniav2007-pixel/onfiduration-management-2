
import csv
import io
import re
import sys
import tarfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen
from urllib.error import HTTPError, URLError


def load_config(filename):
    config = {}

    with open(filename, "r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        if reader.fieldnames != ["parameter", "value"]:
            raise ValueError("Неверный формат CSV")

        for row in reader:
            if None in row or None in row.values():
                raise ValueError("Неверное количество столбцов CSV")

            key = row["parameter"].strip()
            value = row["value"].strip()

            if key in config:
                raise ValueError(f"Повторяющийся параметр: {key}")

            config[key] = value

    return config


def validate_config(config):
    required = {
        "package_name",
        "repository",
        "test_mode",
        "version"
    }

    missing = required - config.keys()
    unknown = config.keys() - required

    if missing:
        raise ValueError(
            f"Отсутствуют параметры: {', '.join(sorted(missing))}"
        )

    if unknown:
        raise ValueError(
            f"Неизвестные параметры: {', '.join(sorted(unknown))}"
        )

    name = config["package_name"]

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise ValueError("Некорректное имя пакета")

    version = config["version"]

    if not re.fullmatch(
        r"\d+(?:\.\d+)+(?:[-+][A-Za-z0-9._-]+)?",
        version
    ):
        raise ValueError("Некорректная версия пакета")

    mode = config["test_mode"].lower()

    if mode not in ("true", "false"):
        raise ValueError("test_mode должен быть true или false")

    config["test_mode"] = mode

    repository = config["repository"]

    if not repository:
        raise ValueError("Репозиторий не указан")

    if mode == "true":
        if not Path(repository).is_file():
            raise ValueError("Тестовый репозиторий не найден")
    else:
        parsed = urlparse(repository)

        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("Некорректный URL репозитория")


def parse_index(text):
    packages = []

    for block in re.split(r"\r?\n\r?\n", text):
        if not block.strip():
            continue

        package = {}

        for line in block.splitlines():
            if ":" not in line:
                continue

            key, value = line.split(":", 1)
            package[key] = value.strip()

        if "P" in package and "V" in package:
            packages.append(package)

    return packages


def read_index_archive(data):
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        member = next(
            (item for item in archive.getmembers()
             if item.name.lstrip("./") == "APKINDEX"
             and item.isfile()),
            None
        )

        if member is None:
            raise ValueError("В архиве отсутствует APKINDEX")

        file = archive.extractfile(member)

        if file is None:
            raise ValueError("Не удалось прочитать APKINDEX")

        return file.read().decode("utf-8")


def get_repository_data(config):
    repository = config["repository"]

    if config["test_mode"] == "true":
        path = Path(repository)

        if path.name.endswith(".tar.gz"):
            data = path.read_bytes()
            return read_index_archive(data)

        return path.read_text(encoding="utf-8-sig")

    if repository.endswith("APKINDEX.tar.gz"):
        url = repository
    else:
        url = repository.rstrip("/") + "/APKINDEX.tar.gz"

    print(f"Загрузка индекса: {url}")

    with urlopen(url, timeout=20) as response:
        data = response.read()

    return read_index_archive(data)


def get_direct_dependencies(packages, name, version):
    for package in packages:
        if package["P"] == name and package["V"] == version:
            return package.get("D", "").split()

    raise ValueError(
        f"Пакет {name} версии {version} не найден в репозитории"
    )


def main():
    filename = sys.argv[1] if len(sys.argv) > 1 else "config.csv"

    try:
        config = load_config(filename)
        validate_config(config)

        index_text = get_repository_data(config)
        packages = parse_index(index_text)

        if not packages:
            raise ValueError("В индексе не найдено записей о пакетах")

        dependencies = get_direct_dependencies(
            packages,
            config["package_name"],
            config["version"]
        )

        print(f"\nПакет: {config['package_name']}")
        print(f"Версия: {config['version']}")
        print("Прямые зависимости:")

        if dependencies:
            for dependency in dependencies:
                print(f"- {dependency}")
        else:
            print("Прямых зависимостей нет")

    except (
        OSError,
        UnicodeError,
        csv.Error,
        ValueError,
        HTTPError,
        URLError,
        tarfile.TarError
    ) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
