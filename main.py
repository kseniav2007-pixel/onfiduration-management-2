
import csv
import re
import sys
from pathlib import Path
from urllib.parse import urlparse


def load_config(filename):
    config = {}

    with open(filename, "r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        if reader.fieldnames != ["parameter", "value"]:
            raise ValueError(
                "Неверный формат CSV. Ожидаются столбцы: parameter,value"
            )

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

    mode = config["test_mode"].lower()

    if mode not in ("true", "false"):
        raise ValueError("test_mode должен быть true или false")

    config["test_mode"] = mode

    repository = config["repository"]

    if not repository:
        raise ValueError("Путь или URL репозитория не указан")

    if mode == "true":
        path = Path(repository)

        if not path.is_file():
            raise ValueError(
                "Файл тестового репозитория не существует"
            )
    else:
        parsed = urlparse(repository)

        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(
                "Репозиторий должен иметь корректный HTTP(S) URL"
            )

    version = config["version"]

    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", version):
        raise ValueError(
            "Некорректная версия пакета. Пример: 1.2.3"
        )


def main():
    filename = sys.argv[1] if len(sys.argv) > 1 else "config.csv"

    try:
        config = load_config(filename)
        validate_config(config)

        print("Параметры конфигурации:")
        for key, value in config.items():
            print(f"{key}: {value}")

    except (OSError, UnicodeError, csv.Error, ValueError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
