from queue import Queue
from typing import Dict

from app.controllers.manager.base_manager import TaskManager


class InMemoryTaskManager(TaskManager):
    def create_queue(self):
        return Queue(maxsize=self.max_queued_tasks)

    def enqueue(self, task: Dict):
        self.queue.put(task)

    def dequeue(self):
        return self.queue.get()

    def is_queue_empty(self):
        return self.queue.empty()

    def queue_size(self):
        return self.queue.qsize()

    def remove_from_queue(self, task_id: str) -> bool:
        removed = False
        kept_tasks = []

        while not self.queue.empty():
            task = self.queue.get()
            queued_task_id = task.get("kwargs", {}).get("task_id")
            if queued_task_id == task_id:
                removed = True
                continue
            kept_tasks.append(task)

        for task in kept_tasks:
            self.queue.put(task)

        return removed
