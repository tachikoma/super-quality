"""기본 팩터 클래스와 인터페이스.

모든 팩터 구현체는 :class:`Factor`를 상속받으며
:meth:`Factor.compute` 메서드와 :attr:`Factor.name`
프로퍼티를 구현해야 합니다.
"""

from abc import ABC, abstractmethod

import pandas as pd


class Factor(ABC):
    """모든 팩터 계산을 위한 추상 기본 클래스.

    서브클래스는 :meth:`compute`와 :attr:`name`을 구현해야 합니다.
    """

    @abstractmethod
    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        """제공된 데이터로부터 팩터 값을 계산합니다.

        Parameters
        ----------
        data : pd.DataFrame
            특정 팩터 구현에 필요한 컬럼을 포함하는 입력 데이터.

        Returns
        -------
        pd.DataFrame
            *data*로부터 계산된 팩터 컬럼을 포함하는 DataFrame.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """사람이 읽을 수 있는 팩터 이름."""
        ...
