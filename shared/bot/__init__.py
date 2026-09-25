"""Бот целиком: ядро и фичи, собранные в один класс.

Каждая фича — отдельный модуль с классом-примесью; все они работают через общее ядро
(`self.repo`, `self.send`, `self.schedule`, `self.ai`, …).
"""

from .cards_flow import CardsFlow
from .compose_flow import ComposeFlow
from .core import BotCore, Ctx
from .exercises_flow import ExercisesFlow
from .generation_flow import GenerationFlow
from .library import LibraryFlow
from .practice_flow import PracticeFlow


class Bot(CardsFlow, ComposeFlow, LibraryFlow, GenerationFlow, PracticeFlow, ExercisesFlow, BotCore):
    pass


__all__ = ["Bot", "Ctx"]
