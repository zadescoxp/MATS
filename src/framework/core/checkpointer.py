from contextlib import AbstractContextManager

from langgraph.checkpoint.sqlite import SqliteSaver

from framework.persistence.database import Database


class GraphCheckpointer:
    def __init__(self, database: Database):
        self._manager: AbstractContextManager[SqliteSaver] | None = None
        if str(database.path) == ":memory:":
            self.saver = SqliteSaver(database.connection)
        else:
            self._manager = SqliteSaver.from_conn_string(str(database.path))
            self.saver = self._manager.__enter__()
        self.saver.setup()

    def close(self) -> None:
        if self._manager is not None:
            self._manager.__exit__(None, None, None)
