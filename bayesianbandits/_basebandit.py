from copy import deepcopy
from dataclasses import Field, dataclass, field
from functools import cached_property, partial
from typing import (
    Any,
    Callable,
    ClassVar,
    Dict,
    MutableMapping,
    Optional,
    Type,
    TypeVar,
    Union,
    cast,
    overload,
)

import numpy as np
from numpy.typing import ArrayLike
from sklearn.base import clone
from typing_extensions import dataclass_transform

from ._typing import ArmProtocol, BanditProtocol, Learner

_B = TypeVar("_B", bound="Bandit")


@dataclass_transform(field_specifiers=(Field, field))
@dataclass
class Bandit:
    """
    Base class for bandits. This class is not meant to be instantiated directly.
    Instead, it should be subclassed and the `learner` and `policy` arguments to
    the `__init_subclass__` method should be set. Optionally, the `delayed_reward`
    argument can be set to `True` to enable delayed rewards.

    By default, `Bandit` subclasses take the following arguments to their
    `__init__` method:

    Subclass Parameters
    -------------------
    learner : Learner
        Learner underlying each arm. Must implement `partial_fit` and `sample`.
    policy : Callable[..., ArmProtocol]
        Policy for choosing arms. Must take a `Bandit` instance as its first
        argument and return an `ArmProtocol` instance.
    delayed_reward : bool, optional
        Whether or not rewards are measured between pulls, by default False.


    Parameters
    ----------
    rng : Union[np.random.Generator, int, None], optional
        Random number generator to use for choosing arms, by default None
    cache : Optional[MutableMapping[Any, str]], optional
        Cache to use for storing arms when `delayed_reward` is set to `True`,
        by default None. If `delayed_reward` is set to `True` and `cache` is
        not set, a `dict` will be used.

    Attributes
    ----------
    learner : Learner
        Learner underlying each arm. Must implement `partial_fit` and `sample`.
    policy : Callable[..., ArmProtocol]
        Policy for choosing arms. Must take a `Bandit` instance as its first
        argument and return an `ArmProtocol` instance.
    rng: Union[np.random.Generator, None, int]
        Random number generator to use for choosing arms.
    arms: Dict[str, ArmProtocol]
        Dictionary of arms.
    last_arm_pulled: Optional[ArmProtocol]
        Last arm pulled.
    cache : Optional[MutableMapping[Any, str]]
        Cache to use for storing arms when `delayed_reward` is set to `True`.

    Examples
    --------

    Minimally, a subclass of `Bandit` must pass a `learner` and `policy` to
    `__init_subclass__`. Additionally, all subclasses must define some
    `ArmProtocol` instances as class attributes. These will be used to
    initialize the arms of the bandit.

    >>> from bayesianbandits import Arm, GammaRegressor, epsilon_greedy
    >>> clf = GammaRegressor(alpha=1, beta=1)
    >>> policy = epsilon_greedy(0.1)
    >>> def action_func():
    ...     print("Action!")
    >>> def reward_func(x):
    ...     return x
    >>> class MyBandit(Bandit, learner=clf, policy=policy):
    ...     arm1 = Arm(action_func, reward_func)
    ...     arm2 = Arm(action_func, reward_func)
    >>> bandit = MyBandit()

    Once subclassed and instantiated, the `pull` method can be used to pull
    arms. For non-contextual bandits, the `pull` method takes no arguments.
    After pulling an arm, the `update` method can be used to update the
    learner underlying the arm.

    >>> bandit.pull()
    Action!
    >>> bandit.update(1)

    For delayed reward bandits, the subclass must set the `delayed_reward`
    argument to `__init_subclass__` to `True`.

    >>> class MyDelayedBandit(Bandit, learner=clf, policy=policy, delayed_reward=True):
    ...     arm1 = Arm(action_func, reward_func)
    ...     arm2 = Arm(action_func, reward_func)
    >>> bandit = MyDelayedBandit(cache={})

    When `delayed_reward` is set to `True`, the `pull` method takes an additional
    argument, `unique_id`, which is used to identify the arm pulled. This is
    used to retrieve the arm from the `cache` when the `update` method is called.

    >>> bandit.pull(unique_id=1)
    Action!
    >>> bandit.update(1, unique_id=1)

    """

    rng: Union[np.random.Generator, None, int] = field(default=None, repr=False)
    last_arm_pulled: Optional[ArmProtocol] = field(default=None, init=False, repr=False)
    cache: Optional[MutableMapping[Any, str]] = field(default=None, repr=False)

    learner: ClassVar[Learner]
    policy: ClassVar[Callable[..., ArmProtocol]]
    _delayed_reward: ClassVar[bool] = False

    def __init_subclass__(
        cls,
        /,
        learner: Learner,
        policy: Callable[..., ArmProtocol],
        delayed_reward: bool = False,
        **kwargs,
    ):
        """Initialize a subclass of `Bandit`.

        Parameters
        ----------
        learner : Learner
            Learner to use for each arm.
        policy : Callable[..., ArmProtocol]
            Policy to use for choosing arms.
        """
        super().__init_subclass__(**kwargs)

        # set learner and policy as class variables
        cls.learner = learner
        cls.policy = policy

        # if delayed reward, set cache as an initializable instance variable,
        # otherwise leave it uninitializable
        if delayed_reward is True:
            cls._delayed_reward = True
            setattr(cls, "cache", field(default_factory=dict, repr=False))
        else:
            setattr(cls, "cache", field(default=None, init=False, repr=False))

        # make sure cache is annotated for dataclass magic
        annotations: dict[str, Any] = getattr(cls, "__annotations__", {})
        annotations["cache"] = Optional[MutableMapping[Any, ArmProtocol]]

        # collect and annotate all ArmProtocol instances
        arm_annotations = {}

        for name, attr in cls.__dict__.items():
            if isinstance(attr, ArmProtocol):
                arm_annotations[name] = ArmProtocol
                setattr(
                    cls,
                    name,
                    field(default_factory=partial(deepcopy, attr), init=False),
                )

        # make sure all ArmProtocol instances are first in the annotations
        # for dataclass magic - this is unnecessary in Python 3.10
        arm_annotations.update(
            {k: v for k, v in annotations.items() if k not in arm_annotations}
        )

        setattr(cls, "__annotations__", arm_annotations)

        # modifies cls in-place
        dataclass(cls)

    @overload
    def pull(self, X: ArrayLike, /) -> None:
        ...

    @overload
    def pull(self, X: ArrayLike, /, *, unique_id: Any) -> None:
        ...

    @overload
    def pull(self, /) -> None:
        ...

    @overload
    def pull(self, /, *, unique_id: Any) -> None:
        ...

    def pull(
        self,
        X: Optional[ArrayLike] = None,
        /,
        **kwargs: Any,
    ) -> None:
        """Choose an arm and pull it. Set `last_arm_pulled` to the name of the
        arm that was pulled.

        This method is added to the bandit class by the `bandit` decorator.

        Parameters
        ----------
        X : Optional[ArrayLike]
            Context for the bandit. Only provided when the @contextual
            decorator is used.

        Options
        -------
        unique_id : Any
            Unique identifier for the pull. Required when the `@delayed_reward`
            decorator is used.
        """

        if X is None and self._contextual:
            raise ValueError("X must be an array-like for a contextual bandit.")
        elif X is not None and not self._contextual:
            raise ValueError("X must be None for a non-contextual bandit.")

        arm = self.policy(X)
        self.last_arm_pulled = arm

        if self.__class__._delayed_reward is True:
            unique_id = kwargs.get("unique_id")
            if unique_id is None:
                raise ValueError(
                    "The `unique_id` keyword argument is required when the "
                    "`delayed_reward = True`."
                )

        ret_val = arm.pull()

        if self.__class__._delayed_reward is True:
            (self.cache[unique_id],) = [  # type: ignore
                k for k, v in self.arms.items() if v is self.last_arm_pulled
            ]

        return ret_val

    @overload
    def update(self, y: ArrayLike, /) -> None:
        ...

    @overload
    def update(self, y: ArrayLike, /, *, unique_id: Any) -> None:
        ...

    @overload
    def update(self, X: ArrayLike, y: ArrayLike, /) -> None:
        ...

    @overload
    def update(self, X: ArrayLike, y: ArrayLike, /, *, unique_id: Any) -> None:
        ...

    def update(
        self,
        X: ArrayLike,
        y: Optional[ArrayLike] = None,
        /,
        **kwargs: Any,
    ) -> None:
        """Update the learner for the last arm pulled.

        This method is added to the bandit class by the `bandit` decorator.

        Parameters
        ----------
        X: ArrayLike
            Context for the bandit. Only provided when the @contextual
            decorator is used.
        y : ArrayLike
            Outcome for the last arm pulled.

        Raises
        ------
        ValueError
            If no arm has been pulled yet.

        Options
        -------
        unique_id : Any
            Unique identifier for the pull. Required when the `@delayed_reward`
            decorator is used.
        """
        if y is None and self._contextual:
            raise ValueError(
                "X and y must both be array-likes for a contextual bandit."
            )
        elif y is not None and not self._contextual:
            raise ValueError(
                "The second argument must be None for a non-contextual bandit."
                " The first argument to `update` must be the outcome."
            )

        if self.__class__._delayed_reward is True:
            unique_id = kwargs.get("unique_id")
            if unique_id is None:
                raise ValueError(
                    "The `unique_id` keyword argument is required when the "
                    "`delayed_reward = True`."
                )
            arm_to_update = self.arms[self.cache.pop(unique_id)]  # type: ignore

        else:
            arm_to_update = cast(ArmProtocol, self.last_arm_pulled)

        arm_to_update.update(X, y)

    @overload
    def sample(self, X: ArrayLike, /, *, size: int = 1) -> ArrayLike:
        ...

    @overload
    def sample(self, /, *, size: int = 1) -> ArrayLike:
        ...

    def sample(
        self,
        X: Optional[ArrayLike] = None,
        /,
        *,
        size: int = 1,
        **kwargs: Any,
    ) -> ArrayLike:
        """Sample from the bandit by choosing an arm according to the
        context vector `X`. For each sample, the arm is chosen according
        to the `policy` algorithm and then a sample is drawn from the
        learner.

        Parameters
        ----------
        X : Optional[ArrayLike]
            Context for the bandit. Only provided when the @contextual
            decorator is used.
        size : int, default=1
            Number of samples to draw.

        """
        if X is None and self._contextual:
            raise ValueError("X must be an array-like for a contextual bandit.")
        elif X is not None and not self._contextual:
            raise ValueError("X must be None for a non-contextual bandit.")
        # choose an arm, draw a sample, and repeat `size` times
        # TODO: this is not the most efficient way to do this
        # but I can't imagine a situation where this would be a bottleneck.
        return np.array([self.policy(X).sample(X) for _ in range(size)])

    def __post_init__(self) -> None:
        """Moves all class attributes that are instances of `Arm` to instance
        attributes.

        This ensures that the bandit can be pickled."""

        self.rng = np.random.default_rng(self.rng)
        self._contextual = False

        # initialize the arms with copies of the learner and
        # point the learner rng to the bandit rng
        for arm in self.arms.values():
            arm.learner = cast(Learner, clone(self.learner))  # type: ignore
            arm.learner.set_params(random_state=self.rng)

        if ArmProtocol not in self.__annotations__.values():
            raise ValueError(
                "A bandit must have at least one arm. "
                "Add an arm to the class definition."
            )

    def __setstate__(self, state: Dict[str, Any]) -> None:
        """Set the state of the bandit.

        This method is called by pickle when loading a bandit from disk.
        This implementation ensures that arms that are no longer defined
        are deleted and arms that are newly defined are initialized, while
        preserving the state of the arms that are still defined.
        """
        self.__dict__.update(state)

        currently_defined_arms = {
            name: attr
            for name, attr in self.__class__.__annotations__.items()
            if attr is ArmProtocol
        }

        # delete arms that are no longer defined
        to_del = [
            arm_name for arm_name in self.arms if arm_name not in currently_defined_arms
        ]

        for arm_name in to_del:
            del self.arms[arm_name]
            del self.__dict__[arm_name]

        # initialize arms that are newly defined
        for arm_name, arm in currently_defined_arms.items():
            if arm_name not in self.arms:
                new_arm = self.__class__.__dataclass_fields__[
                    arm_name
                ].default_factory()
                new_arm.learner = cast(Learner, clone(self.learner))  # type: ignore
                setattr(self, arm_name, new_arm)  # type: ignore
                self.arms[arm_name] = self.__dict__[arm_name]

    @cached_property
    def arms(self: BanditProtocol) -> Dict[str, ArmProtocol]:
        return {
            name: attr
            for name, attr in self.__dict__.items()
            if isinstance(attr, ArmProtocol)
        }


def contextual(
    cls: Type[_B],
) -> Type[_B]:
    """Decorator for making a bandit contextual.

    This decorator adds methods to a Bandit subclass that allow it to be used
    in a contextual setting. The `pull` and `sample` methods will take `X`
    arguments, and the `update` method will take `X` and `y`.

    Parameters
    ----------
    cls : Bandit
        Bandit class to make contextual.

    Returns
    -------
    Bandit
        Contextual bandit class.

    Examples
    --------

    A contextual bandit can be created by decorating a Bandit subclass with
    the `contextual` decorator.

    >>> from bayesianbandits import Arm, GammaRegressor, epsilon_greedy
    >>> clf = GammaRegressor(alpha=1, beta=1)
    >>> policy = epsilon_greedy(0.1)
    >>> def action_func():
    ...     print("Action!")
    >>> def reward_func(x):
    ...     return x
    >>> @contextual
    ... class MyBandit(Bandit, learner=clf, policy=policy):
    ...     arm1 = Arm(action_func, reward_func)
    ...     arm2 = Arm(action_func, reward_func)
    >>> bandit = MyBandit()

    The `pull`, `sample`, and `update` methods now take an `X` argument.

    >>> bandit.pull(1)
    Action!
    >>> bandit.update(1, 1)

    """

    check_is_bandit(cls)

    orig_post_init = cls.__post_init__  # type: ignore

    def _contextual_post_init(self) -> None:
        orig_post_init(self)
        self._contextual = True

    setattr(cls, "__post_init__", _contextual_post_init)

    return cls


def restless(
    cls: Type[_B],
) -> Type[_B]:
    """Decorator for restless bandits.

    This decorator ensures that the `decay` method of each unselected
    arm is called at each update. The specific behavior of the `decay`
    method is left to the learner.

    """

    check_is_bandit(cls)

    orig_update = cls.update  # type: ignore

    def _restless_update(
        self: Bandit,
        X: ArrayLike,
        y: Optional[ArrayLike] = None,
        **kwargs: ArrayLike,
    ) -> None:
        orig_update(self, X, y, **kwargs)  # type: ignore
        for arm in self.arms.values():
            if arm is not self.last_arm_pulled:
                arm.decay(X, y)

    setattr(cls, "update", _restless_update)

    return cast(Type[_B], cls)


def check_is_bandit(cls):
    if not issubclass(cls, Bandit):
        raise ValueError("This decorator can only be used on a Bandit subclass.")
