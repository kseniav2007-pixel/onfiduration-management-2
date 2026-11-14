
import csv
import io
import re
import sys
import tarfile
from collections import deque
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
    version = config["version"]
    mode = config["test_mode"].lower()
    repository = config["repository"]

    if mode not in ("true", "false"):
        raise ValueError("test_mode должен быть true или false")

    config["test_mode"] = mode

    if not repository:
        raise ValueError("Репозиторий не указан")

    if not version:
        raise ValueError("Версия пакета не указана")

    if mode == "true":
        if not re.fullmatch(r"[A-Z]+", name):
            raise ValueError(
                "В тестовом режиме используются только "
                "большие латинские буквы"
            )

        if not Path(repository).is_file():
            raise ValueError("Тестовый репозиторий не найден")
    else:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", name):
            raise ValueError("Некорректное имя пакета")

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
            (
                item for item in archive.getmembers()
                if item.name.lstrip("./") == "APKINDEX"
                and item.isfile()
            ),
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
        return Path(repository).read_text(encoding="utf-8-sig")

    if repository.endswith("APKINDEX.tar.gz"):
        url = repository
    else:
        url = repository.rstrip("/") + "/APKINDEX.tar.gz"

    print(f"Загрузка индекса: {url}")

    with urlopen(url, timeout=20) as response:
        data = response.read()

    return read_index_archive(data)


def parse_test_repository(text):
    graph = {}

    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()

        if not line or line.startswith("#"):
            continue

        if ":" not in line:
            raise ValueError(
                f"Строка {number}: отсутствует двоеточие"
            )

        name, dependencies = line.split(":", 1)
        name = name.strip()
        items = dependencies.split()

        if not re.fullmatch(r"[A-Z]+", name):
            raise ValueError(
                f"Строка {number}: некорректное имя {name}"
            )

        if name in graph:
            raise ValueError(f"Повторяющийся пакет: {name}")

        for dependency in items:
            if not re.fullmatch(r"[A-Z]+", dependency):
                raise ValueError(
                    f"Некорректная зависимость: {dependency}"
                )

        graph[name] = list(dict.fromkeys(items))

    for name, dependencies in graph.items():
        for dependency in dependencies:
            if dependency not in graph:
                raise ValueError(
                    f"Пакет {name} зависит от "
                    f"неизвестного пакета {dependency}"
                )

    return graph


def dependency_name(dependency):
    if dependency.startswith("!"):
        return None

    if ":" in dependency.split("=")[0]:
        return dependency

    name = re.split(r"[<>=~]", dependency, maxsplit=1)[0]

    return name if name else None


def build_package_index(packages):
    index = {}

    for package in packages:
        name = package["P"]

        if name not in index:
            index[name] = package

    return index


def make_real_dependency_provider(packages, root_name, root_version):
    root = next(
        (
            package for package in packages
            if package["P"] == root_name
            and package["V"] == root_version
        ),
        None
    )

    if root is None:
        raise ValueError(
            f"Пакет {root_name} версии {root_version} не найден"
        )

    index = build_package_index(packages)

    def get_dependencies(name):
        if name == root_name:
            package = root
        else:
            package = index.get(name)

        if package is None:
            return []

        result = []

        for raw in package.get("D", "").split():
            dependency = dependency_name(raw)

            if dependency and dependency not in result:
                result.append(dependency)

        return result

    return get_dependencies


def bfs_recursive(queue, visited, graph, get_dependencies):
    if not queue:
        return

    current = queue.popleft()
    dependencies = get_dependencies(current)
    graph[current] = dependencies

    for dependency in dependencies:
        if dependency not in visited:
            visited.add(dependency)
            queue.append(dependency)

    bfs_recursive(queue, visited, graph, get_dependencies)


def build_graph(start, get_dependencies):
    queue = deque([start])
    visited = {start}
    graph = {}

    bfs_recursive(queue, visited, graph, get_dependencies)

    return graph


def find_cycles(graph):
    color = {}
    path = []
    cycles = []

    def visit(node):
        color[node] = 1
        path.append(node)

        for neighbor in graph.get(node, []):
            if color.get(neighbor, 0) == 0:
                visit(neighbor)
            elif color.get(neighbor) == 1:
                start = path.index(neighbor)
                cycle = path[start:] + [neighbor]

                if cycle not in cycles:
                    cycles.append(cycle)

        path.pop()
        color[node] = 2

    for node in graph:
        if color.get(node, 0) == 0:
            visit(node)

    return cycles


def get_load_order(graph, start):
    order = []
    state = {}
    path = []

    def visit(package):
        status = state.get(package, 0)

        if status == 1:
            position = path.index(package)
            cycle = path[position:] + [package]
            raise ValueError(
                "Невозможно определить порядок загрузки. "
                "Цикл: " + " -> ".join(cycle)
            )

        if status == 2:
            return

        if package not in graph:
            raise ValueError(
                f"Не найдены данные о зависимости: {package}"
            )

        state[package] = 1
        path.append(package)

        for dependency in graph[package]:
            visit(dependency)

        path.pop()
        state[package] = 2
        order.append(package)

    visit(start)

    return order


def print_graph(graph, start):
    print(f"\nГраф зависимостей пакета {start}:")

    for package, dependencies in graph.items():
        if dependencies:
            print(f"{package} -> {', '.join(dependencies)}")
        else:
            print(f"{package} -> нет зависимостей")

    all_dependencies = set(graph)
    all_dependencies.discard(start)

    print("\nВсе достижимые зависимости:")

    if all_dependencies:
        print(", ".join(sorted(all_dependencies)))
    else:
        print("Нет зависимостей")

    cycles = find_cycles(graph)

    print("\nПроверка циклов:")

    if cycles:
        for cycle in cycles:
            print("Обнаружен цикл: " + " -> ".join(cycle))
    else:
        print("Циклов не обнаружено")


def print_load_order(graph, start):
    print("\nПорядок загрузки зависимостей:")

    try:
        order = get_load_order(graph, start)

        for number, package in enumerate(order, start=1):
            print(f"{number}. {package}")

    except ValueError as error:
        print(f"Порядок загрузки не определён: {error}")


def main():
    filename = sys.argv[1] if len(sys.argv) > 1 else "config.csv"

    try:
        config = load_config(filename)
        validate_config(config)

        name = config["package_name"]

        if config["test_mode"] == "true":
            text = get_repository_data(config)
            test_graph = parse_test_repository(text)

            if name not in test_graph:
                raise ValueError(
                    f"Пакет {name} отсутствует в тестовом репозитории"
                )

            def get_dependencies(package):
                return test_graph[package]
        else:
            text = get_repository_data(config)
            packages = parse_index(text)

            if not packages:
                raise ValueError("Индекс пакетов пуст")

            get_dependencies = make_real_dependency_provider(
                packages,
                name,
                config["version"]
            )

        graph = build_graph(name, get_dependencies)

        print_graph(graph, name)
        print_load_order(graph, name)

    except (
        OSError,
        UnicodeError,
        csv.Error,
        ValueError,
        HTTPError,
        URLError,
        tarfile.TarError,
        RecursionError
    ) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
