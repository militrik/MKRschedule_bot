from aiogram.types import MessageEntity
from aiogram.enums import MessageEntityType

class EntityBuilder:
    """
    Допоміжний будівник повідомлень:
      - збирає текст частинами;
      - дозволяє додавати фрагменти з жирним накресленням як message entities;
      - повертає (text, entities) для send_message(..., entities=entities).
    """
    def __init__(self):
        self._parts: list[str] = []
        self._entities: list[MessageEntity] = []

    def _len(self) -> int:
        return sum(len(p) for p in self._parts)

    def add(self, text: str) -> "EntityBuilder":
        self._parts.append(text)
        return self

    def add_bold(self, text: str) -> "EntityBuilder":
        start = self._len()
        self._parts.append(text)
        if text:
            self._entities.append(
                MessageEntity(type=MessageEntityType.BOLD, offset=start, length=len(text))
            )
        return self

    def newline(self) -> "EntityBuilder":
        self._parts.append("\n")
        return self

    def build(self) -> tuple[str, list[MessageEntity]]:
        return "".join(self._parts), self._entities
