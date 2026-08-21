"""World object registry: create objects and report object_count."""


class World:
    """A minimal world tracking created objects."""

    def __init__(self) -> None:
        self._objects = {}
        self._next_id = 0

    @property
    def object_count(self) -> int:
        """当前世界中的对象总数。"""
        return len(self._objects)

    def create_object(self, name: str = "object") -> int:
        """在世界中创建一个新对象，返回其 id，object_count 随之 +1。"""
        obj_id = self._next_id
        self._next_id += 1
        self._objects[obj_id] = {"id": obj_id, "name": name}
        return obj_id


def main() -> None:
    """在世界中创建一个新对象，并打印新的 object_count。"""
    world = World()
    world.create_object("new_object")
    print(world.object_count)  # 初始为空，创建 1 个对象后为 1


if __name__ == "__main__":
    main()
