"""The Continuous Predator-Prey Environment.

A co-operative 2D continuous world problem involving multiple predator agents working
together to catch prey agents in the environment.

This intends to be an adaptation of the 2D grid-world to the continuous setting.

Reference
---------
- Ming Tan. 1993. Multi-Agent Reinforcement Learning: Independent vs. Cooperative Agents
  In Proceedings of the Tenth International Conference on Machine Learning. 330–337.
- J. Z. Leibo, V. F. Zambaldi, M. Lanctot, J. Marecki, and T. Graepel. 2017. Multi-Agent
  Reinforcement Learning in Sequential Social Dilemmas. In AAMAS, Vol. 16. ACM, 464–473
- Lowe, Ryan, Yi I. Wu, Aviv Tamar, Jean Harb, OpenAI Pieter Abbeel, and Igor Mordatch.
  2017. “Multi-Agent Actor-Critic for Mixed Cooperative-Competitive Environments.”
  Advances in Neural Information Processing Systems 30.

"""
import math
from itertools import product
from typing import Dict, List, NamedTuple, Optional, Set, Tuple, Union, cast

import numpy as np
import pymunk
from gymnasium import spaces
from pymunk import Vec2d

import posggym.model as M
from posggym import logger
from posggym.core import DefaultEnv
from posggym.envs.continuous.core2 import (
    CircleEntity,
    PMBodyState,
    Position,
    SquareContinuousWorld,
    clip_actions,
)
from posggym.utils import seeding


class PPState(NamedTuple):
    """A state in the Continuous Predator-Prey Environment."""

    predator_states: np.ndarray
    prey_states: np.ndarray
    prey_caught: np.ndarray


PPObs = np.ndarray
PPAction = np.ndarray


class PredatorPreyContinuous(DefaultEnv[PPState, PPObs, PPAction]):
    """The Continuous Predator-Prey Environment.

    A co-operative 2D continuous world problem involving multiple predator agents
    working together to catch prey agent/s in the environment.

    Possible Agents
    ---------------
    Varied number

    State Space
    -----------
    Each state consists of:

    1. tuple of the (x, y) position of all predators
    2. tuple of the (x, y) position of all preys
    3. tuple of whether each prey has been caught or not (0=no, 1=yes)

    For the coordinate x=column, y=row, with the origin (0, 0) at the top-left square
    of the world.

    Action Space
    ------------
    Each agent's actions is made up of two parts, which depend on the dynamics model
    used by the environment.

    In the `holonomic` the action components specify the velocity along the x and y
    axes, respectively. With possible values in `[-1, 1]`.

    In the non-holonomic model, the first action component specifies the angular
    velocity in `[-2*pi, 2*pi]`, and the second component specifies the linear velocity
    in `[0, 1]`.

    Observation Space
    -----------------
    Each agent observes a local circle around themselves as a vector. This is achieved
    by a series of 'n_sensors' lines starting at the agent which extend for a distance
    of 'obs_dist'. For each line the agent observes the closest entity (wall, predator,
    prey) along the line. This table enumerates the observation space:

    |        Index: [start, end)        | Description                       | Values |
    | :-------------------------------: | --------------------------------: | :----: |
    |           0 - n_sensors           | Wall distance for each sensor     | [0, d] |
    |    n_sensors - (2 * n_sensors)    | Predator distance for each sensor | [0, d] |
    | (2 * n_sensors) - (3 * n_sensors) | Prey distance for each sensor     | [0, d] |


    Where `d = obs_dist`.

    If an entity is not observed (i.e. there is none along the sensor's line or it
    isn't the closest entity to the observing agent along the line), The distance will
    be 1.

    The sensor reading ordering is relative to the agent's direction. I.e. the values
    for the first sensor at indices `0`, `n_sensors`, `2*n_sensors` correspond to the
    distance reading to a wall/obstacle, predator, and prey, respectively, in the
    direction the agent is facing.

    Rewards
    -------
    There are two modes of play:

    1. *Fully cooperative*: All predators share a reward and each agent receives
    a reward of `1.0 / num_prey` for each prey capture, independent of which
    predator agent/s were responsible for the capture.

    2. *Mixed cooperative*: Predators only receive a reward if they were part
    of the prey capture, receiving `1.0 / num_prey` per capture.

    In both modes prey can only been captured when at least `prey_strength`
    predators are in adjacent cells, where `1 <= prey_strength <= num_predators`.

    Dynamics
    --------
    Actions of the predator agents are deterministic and consist of moving based on
    the dynamic model. If two or more predators attempt to move into the same location
    then no agent moves.

    Prey move according to the following rules (in order of priority):

    1. if predator is within `obs_dist` distance, moves away from closest predator
    2. if another prey is within `obs_dist` distance, moves away from closest prey
    3. else move randomly

    Prey always move first and predators and prey cannot occupy the same location.
    The only exception being if a prey has been caught their final coord is
    recorded in the state but predator and prey agents will be able to move
    into the final coord.

    Starting State
    --------------
    Predators start from random separate locations along the edge of the world
    (either in a corner, or half-way along a side), while prey start together
    in the middle.

    Episodes End
    ------------
    Episodes ends when all prey have been captured. By default a `max_episode_steps`
    limit of `50` steps is also set. This may need to be adjusted when using larger
    worlds (this can be done by manually specifying a value for `max_episode_steps` when
    creating the environment with `posggym.make`).

    Arguments
    ---------

    - `world` - the world layout to use. This can either be a string specifying one of
        the supported worlds, or a custom :class:`PPWorld` object (default = `"10x10"`).
    - `num_predators` - the number of predator (and thus controlled agents)
        (default = `2`).
    - `num_prey` - the number of prey (default = `3`)
    - `cooperative` - whether agents share all rewards or only get rewards for prey they
        are involved in capturing (default = 'True`)
    - `prey_strength` - how many predators are required to capture each prey, minimum is
        `1` and maximum is `min(4, num_predators)`. If `None` this is set to
        `min(4, num_predators)` (default = 'None`)
    - `obs_dist` - the local observation distance, specifying how far away in each
        direction each predator and prey agent observes (default = `2`).
    - `n_sensors` - the number of lines eminating from the agent. The agent will observe
        at `n` equidistance intervals over `[0, 2*pi]` (default = `10`).
    - `use_holonomic_predator` - the movement model to use for the predator. There are
        two modes - holonomic or non holonmic, with a unicycle model (default = 'True`).
    - `use_holonomic_prey` - the movement model to use for the prey. There are two
        modes - holonomic or non holonmic, with a unicycle model (default = 'True`).

    Available variants
    ------------------

    The PredatorPrey environment comes with a number of pre-built world layouts which
    can be passed as an argument to `posggym.make`, to create different worlds. All
    layouts support 2 to 8 agents.

    | World name         | World size |
    |-------------------|-----------|
    | `5x5`             | 5x5       |
    | `5x5Blocks`       | 5x5       |
    | `10x10`           | 10x10     |
    | `10x10Blocks`     | 10x10     |
    | `15x15`           | 15x15     |
    | `15x15Blocks`     | 15x15     |
    | `20x20`           | 20x20     |
    | `20x20Blocks`     | 20x20     |


    For example to use the Predator Prey environment with the `15x15Blocks` world, 4
    predators, 4 prey, and episode step limit of 100, and the default values for the
    other parameters (`cooperative`, `obs_dist`, `prey_strength`) you would use:

    ```python
    import posggym
    env = posggym.make(
        'PredatorPreyContinuous-v0',
        max_episode_steps=100,
        world="15x15Blocks",
        num_predators=4,
        num_prey=4
    )
    ```

    Version History
    ---------------
    - `v0`: Initial version

    Reference
    ---------
    - Ming Tan. 1993. Multi-Agent Reinforcement Learning: Independent vs. Cooperative
      Agents. In Proceedings of the Tenth International Conference on Machine Learning.
      330–337.
    - J. Z. Leibo, V. F. Zambaldi, M. Lanctot, J. Marecki, and T. Graepel. 2017.
      Multi-Agent Reinforcement Learning in Sequential Social Dilemmas. In AAMAS,
      Vol. 16. ACM, 464–473
    - Lowe, Ryan, Yi I. Wu, Aviv Tamar, Jean Harb, OpenAI Pieter Abbeel, and Igor
      Mordatch. 2017. “Multi-Agent Actor-Critic for Mixed Cooperative-Competitive
      Environments.” Advances in Neural Information Processing Systems 30.

    """

    metadata = {
        "render_modes": ["human", "rgb_array"],
        "render_fps": 15,
    }

    def __init__(
        self,
        world: Union[str, "PPWorld"] = "10x10",
        num_predators: int = 2,
        num_prey: int = 3,
        cooperative: bool = True,
        prey_strength: Optional[int] = None,
        obs_dist: float = 2,
        n_sensors: int = 10,
        use_holonomic: bool = True,
        render_mode: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(
            PPModel(
                world,
                num_predators,
                num_prey,
                cooperative,
                prey_strength,
                obs_dist,
                n_sensors,
                use_holonomic,
            ),
            render_mode=render_mode,
        )
        self._obs_dist = obs_dist
        self.window_surface = None
        self.clock = None
        self.window_size = 600
        self.draw_options = None
        self.world = None

    def render(self):
        if self.render_mode is None:
            assert self.spec is not None
            logger.warn(
                "You are calling render method without specifying any render mode. "
                "You can specify the render_mode at initialization, "
                f'e.g. posggym.make("{self.spec.id}", render_mode="rgb_array")'
            )
            return
        if self.render_mode in ("human", "rgb_array"):
            return self._render_img()
        else:
            logger.warn(
                "You are calling render method on an invalid render mode"
                'Continuous environments currently only support "human" or'
                '"rgb_array" render modes.'
                "You can specify the render_mode at initialization, "
                f'e.g. posggym.make("{self.spec.id}", render_mode="rgb_array")'
            )
            return

    def _render_img(self):
        # import posggym.envs.continuous.render as render_lib
        import pygame
        from pymunk import pygame_util

        model = cast(PPModel, self.model)
        state = cast(PPState, self.state)
        scale_factor = self.window_size // model.world.size

        if self.window_surface is None:
            pygame.init()
            if self.render_mode == "human":
                pygame.display.init()
                pygame.display.set_caption(self.__class__.__name__)
                self.window_surface = pygame.display.set_mode(
                    (self.window_size, self.window_size)
                )
            else:
                self.window_surface = pygame.Surface(
                    (self.window_size, self.window_size)
                )
            # Turn off alpha since we don't use it.
            self.window_surface.set_alpha(None)

        if self.clock is None:
            self.clock = pygame.time.Clock()

        if self.draw_options is None:
            pygame_util.positive_y_is_up = False
            self.draw_options = pygame_util.DrawOptions(self.window_surface)
            self.draw_options.transform = pymunk.Transform.scaling(scale_factor)
            # don't render collision lines
            self.draw_options.flags = (
                pygame_util.DrawOptions.DRAW_SHAPES
                | pygame_util.DrawOptions.DRAW_CONSTRAINTS
            )

        if self.world is None:
            # get copy of model world, so we can use it for rendering without
            # affecting the original
            self.world = model.world.copy()

        for i, p_state in enumerate(state.predator_states):
            self.world.set_entity_state(f"pred_{i}", p_state)

        for i, p_state in enumerate(state.prey_states):
            self.world.set_entity_state(f"prey_{i}", p_state)

        # Need to do this for space to update with changes
        self.world.space.step(0.0001)

        # reset screen
        self.window_surface.fill(pygame.Color("white"))

        # draw sensor lines
        n_sensors = model.n_sensors
        for i, obs_i in self._last_obs.items():
            p_state = state.predator_states[int(i)]
            x, y, agent_angle = p_state[:3]
            angle_inc = 2 * math.pi / n_sensors
            for k in range(n_sensors):
                dist = min(obs_i[k], obs_i[n_sensors + k], obs_i[2 * n_sensors + k])
                angle = angle_inc * k + agent_angle
                end_x = x + dist * math.cos(angle)
                end_y = y + dist * math.sin(angle)
                scaled_start = (int(x * scale_factor), int(y * scale_factor))
                scaled_end = int(end_x * scale_factor), (end_y * scale_factor)

                pygame.draw.line(
                    self.window_surface, pygame.Color("red"), scaled_start, scaled_end
                )

        self.world.space.debug_draw(self.draw_options)

        if self.render_mode == "human":
            pygame.event.pump()
            pygame.display.update()
            self.clock.tick(self.metadata["render_fps"])
            return None

        return np.transpose(
            np.array(pygame.surfarray.pixels3d(self.window_surface)), axes=(1, 0, 2)
        )

    def close(self) -> None:
        pass


class PPModel(M.POSGModel[PPState, PPObs, PPAction]):
    """Predator-Prey Problem Model.

    Parameters
    ----------
    world_size : float
        the size of the world (height and width)
    num_predators : int
        the number of predator (and thus controlled agents)
    num_prey : int
        the number of prey
    cooperative : bool
        whether environment rewards are fully shared (True) or only awarded to
        capturing predators (i.e. mixed) (False)
    prey_strenth : int
        the minimum number of predators needed to capture a prey
    obs_dists : float
        number of cells in each direction around the agent that the agent can
        observe
    n_sensors : int
        the number of sensor lines for each predator
    use_holonomic : bool
        whether to use holonomic dynamics (each action specifies dx, dy of agent) or
        non-holonomic dynamics (each action is a dyaw, velocity of agent)

    """

    R_MAX = 1.0
    # max distance a predator or prey agent can move in a single step
    STEP_VEL = 0.5
    # distance predator can be from prey to be considered to be within catching range
    COLLISION_DIST = 1.2

    PREDATOR_COLOR = (55, 155, 205, 255)  # Blueish
    PREY_COLOR = (110, 55, 155, 255)  # purpleish

    def __init__(
        self,
        world: Union[str, "PPWorld"],
        num_predators: int,
        num_prey: int,
        cooperative: bool,
        prey_strength: Optional[int],
        obs_dist: float,
        n_sensors: int,
        use_holonomic: bool,
        **kwargs,
    ):
        assert 1 < num_predators <= 8
        assert num_prey > 0
        assert obs_dist > 0

        if prey_strength is None:
            prey_strength = min(4, num_predators)

        assert 0 < prey_strength <= min(4, num_predators)

        if isinstance(world, str):
            assert world in SUPPORTED_WORLDS, (
                f"Unsupported world name '{world}'. World name must be one of: "
                f"{list(SUPPORTED_WORLDS)}."
            )
            world = SUPPORTED_WORLDS[world][0]()
        # Cannot be a string by this point.
        self.world = cast(PPWorld, world)

        assert len(self.world.predator_start_positions) >= num_predators, (
            f"World of size ({self.world.size, self.world.size}) cannot support "
            f"{num_predators} predators. Try with less predators, using a larger world,"
            " or using a different world layout."
        )
        assert len(self.world.prey_start_positions) >= num_prey, (
            f"World of size ({self.world.size, self.world.size}) cannot support "
            f"{num_prey} prey. Try with less prey, using a larger world, or using a "
            "different world layout."
        )

        # self.world.set_holonomic_model(use_holonomic)
        self.num_predators = num_predators
        self.num_prey = num_prey
        self.obs_dist = obs_dist
        self.cooperative = cooperative
        self.prey_strength = prey_strength
        self._per_prey_reward = self.R_MAX / self.num_prey
        self.n_sensors = n_sensors
        self.use_holonomic = use_holonomic
        self.communication_radius = 1

        def _pos_space(n_agents: int):
            # x, y, angle, vx, vy, vangle
            # stacked n_agents time
            # shape = (n_agents, 6)
            size, angle = self.world.size, 2 * math.pi
            low = np.array([-1, -1, -angle, -1, -1, -angle], dtype=np.float32)
            high = np.array(
                [size, size, angle, 1.0, 1.0, angle],
                dtype=np.float32,
            )
            return spaces.Box(
                low=np.tile(low, (n_agents, 1)), high=np.tile(high, (n_agents, 1))
            )

        self.possible_agents = tuple((str(x) for x in range(self.num_predators)))
        self.state_space = spaces.Tuple(
            (
                # state of each predator
                _pos_space(self.num_predators),
                # state of each prey
                _pos_space(self.num_prey),
                # prey caught/not
                spaces.MultiBinary(self.num_prey),
            )
        )

        self.dyaw_limit = math.pi / 10
        if self.use_holonomic:
            # dx, dy
            self.action_spaces = {
                i: spaces.Box(
                    low=np.array([-1.0, -1.0], dtype=np.float32),
                    high=np.array([1.0, 1.0], dtype=np.float32),
                )
                for i in self.possible_agents
            }
        else:
            # dyaw, vel
            self.action_spaces = {
                i: spaces.Box(
                    low=np.array([-self.dyaw_limit, 0.0], dtype=np.float32),
                    high=np.array([self.dyaw_limit, 1.0], dtype=np.float32),
                )
                for i in self.possible_agents
            }

        # Observes entity and distance to entity along a n_sensors rays from the agent
        # 0 to n_sensors = wall distance obs
        # n_sensors to (2 * n_sensors) = pred dist
        # (2 * n_sensors) to (3 * n_sensors) = prey dist
        self.obs_dim = self.n_sensors * 3
        self.observation_spaces = {
            i: spaces.Box(
                low=0.0, high=self.obs_dist, shape=(self.obs_dim,), dtype=np.float32
            )
            for i in self.possible_agents
        }

        # All predators are identical so env is symmetric
        self.is_symmetric = True

        # Add physical entities to the world
        for i in range(self.num_predators):
            self.world.add_entity(f"pred_{i}", None, color=self.PREDATOR_COLOR)

        for i in range(self.num_prey):
            self.world.add_entity(f"prey_{i}", None, color=self.PREY_COLOR)

    @property
    def reward_ranges(self) -> Dict[M.AgentID, Tuple[float, float]]:
        return {i: (0.0, self.R_MAX) for i in self.possible_agents}

    @property
    def rng(self) -> seeding.RNG:
        if self._rng is None:
            self._rng, seed = seeding.std_random()
        return self._rng

    def get_agents(self, state: PPState) -> List[M.AgentID]:
        return list(self.possible_agents)

    def sample_initial_state(self) -> PPState:
        predator_positions = [*self.world.predator_start_positions]
        self.rng.shuffle(predator_positions)
        predator_states = np.zeros(
            (self.num_predators, PMBodyState.num_features()), dtype=np.float32
        )
        for i in range(self.num_predators):
            predator_states[i][:3] = predator_positions[i]

        prey_positions = [*self.world.prey_start_positions]
        self.rng.shuffle(prey_positions)
        prey_states = np.zeros(
            (self.num_prey, PMBodyState.num_features()), dtype=np.float32
        )
        for i in range(self.num_prey):
            prey_states[i][:3] = prey_positions[i]

        return PPState(
            predator_states,
            prey_states,
            np.zeros(self.num_prey, dtype=np.int8),
        )

    def sample_initial_obs(self, state: PPState) -> Dict[M.AgentID, PPObs]:
        return self.get_obs(state)

    def step(
        self, state: PPState, actions: Dict[M.AgentID, PPAction]
    ) -> M.JointTimestep[PPState, PPObs]:
        clipped_actions = clip_actions(actions, self.action_spaces)

        next_state = self._get_next_state(state, clipped_actions)
        obs = self.get_obs(next_state)
        rewards = self._get_rewards(state, next_state)

        all_done = all(next_state.prey_caught)
        truncated = {i: False for i in self.possible_agents}
        terminated = {i: all_done for i in self.possible_agents}

        info: Dict[M.AgentID, Dict] = {i: {} for i in self.possible_agents}
        if all_done:
            for i in self.possible_agents:
                info[i]["outcome"] = M.Outcome.WIN

        return M.JointTimestep(
            next_state, obs, rewards, terminated, truncated, all_done, info
        )

    def _get_next_state(
        self, state: PPState, actions: Dict[M.AgentID, PPAction]
    ) -> PPState:
        prey_move_angles = self._get_prey_move_angles(state)

        # apply prey actions
        for i in range(self.num_prey):
            self.world.set_entity_state(f"prey_{i}", state.prey_states[i])
            if state.prey_caught[i]:
                # do nothing
                continue
            self.world.update_entity_state(
                f"prey_{i}",
                angle=prey_move_angles[i],
                vel=self.STEP_VEL * Vec2d(1, 0).rotated(prey_move_angles[i]),
            )

        # apply predator actions
        for i in range(self.num_predators):
            action = actions[str(i)]
            self.world.set_entity_state(f"pred_{i}", state.predator_states[i])
            if self.use_holonomic:
                angle = math.atan2(action[1], action[0])
                vel = Vec2d(action[0], action[1])
            else:
                angle = state.predator_states[i][2] + action[0]
                vel = action[1] * Vec2d(1, 0).rotated(angle)
            self.world.update_entity_state(f"pred_{i}", angle=angle, vel=vel)

        # simulate
        self.world.simulate(1.0 / 10, 10)

        # extract next state
        next_pred_states = np.array(
            [
                self.world.get_entity_state(f"pred_{i}")
                for i in range(self.num_predators)
            ],
            dtype=np.float32,
        )
        next_prey_states = np.array(
            [self.world.get_entity_state(f"prey_{i}") for i in range(self.num_prey)],
            dtype=np.float32,
        )

        next_prey_caught = state.prey_caught.copy()
        for i in range(self.num_prey):
            if state.prey_caught[i]:
                next_prey_states[i] = [-1.0, -1.0, 0.0, 0.0, 0.0, 0.0]
                continue
            pred_dists = np.linalg.norm(
                next_prey_states[i][:2] - next_pred_states[:, :2], axis=1
            )
            if (
                np.where(pred_dists <= self.COLLISION_DIST, 1, 0).sum()
                >= self.prey_strength
            ):
                next_prey_caught[i] = 1
                next_prey_states[i] = [-1.0, -1.0, 0.0, 0.0, 0.0, 0.0]

        return PPState(next_pred_states, next_prey_states, next_prey_caught)

    def _get_prey_move_angles(self, state: PPState) -> List[float]:
        prey_actions = []
        active_prey = self.num_prey - state.prey_caught.sum()
        for i in range(self.num_prey):
            if state.prey_caught[i]:
                # prey stays in same position
                prey_actions.append(0.0)
                continue

            prey_state = state.prey_states[i]
            # try move away from predators
            pred_states = state.predator_states
            pred_dists = np.linalg.norm(prey_state[:2] - pred_states[:, :2], axis=1)
            min_pred_dist = pred_dists.min()
            if min_pred_dist <= self.obs_dist:
                # get any predators within obs distance
                pred_idx = self.rng.choice(np.where(pred_dists == min_pred_dist)[0])
                pred_state = state.predator_states[pred_idx]
                angle = math.atan2(
                    prey_state[1] - pred_state[1], prey_state[0] - pred_state[0]
                )
                prey_actions.append(angle)
                continue

            if active_prey == 1:
                # no other prey to move away from so just move randomly
                angle = self.rng.uniform(0, 2 * math.pi)
                prey_actions.append(angle)
                continue

            # try move away from prey
            prey_dists = [
                np.linalg.norm(prey_state[:2] - p[:2])
                for j, p in enumerate(state.prey_states)
                if not state.prey_caught[j] and j != i
            ]
            min_prey_dist = min(prey_dists)
            if min_prey_dist <= self.obs_dist:
                other_prey_idx = self.rng.choice(
                    np.where(prey_dists == min_prey_dist)[0]
                )
                other_prey_state = state.prey_states[other_prey_idx]
                angle = math.atan2(
                    prey_state[1] - other_prey_state[1],
                    prey_state[0] - other_prey_state[0],
                )
                prey_actions.append(angle)
                continue

            # move in random direction
            angle = self.rng.uniform(0, 2 * math.pi)
            prey_actions.append(angle)

        return prey_actions

    def get_obs(self, state: PPState) -> Dict[M.AgentID, PPObs]:
        return {i: self._get_local_obs(i, state) for i in self.possible_agents}

    def _get_local_obs(self, agent_id: M.AgentID, state: PPState) -> np.ndarray:
        state_i = state.predator_states[int(agent_id)]
        pos_i = (state_i[0], state_i[1], state_i[2])

        prey_coords = np.array(
            [
                [s[0], s[1]]
                for i, s in enumerate(state.prey_states)
                if not state.prey_caught[i]
            ]
        )
        prey_obs = self.world.check_collision_circular_rays(
            pos_i,
            self.obs_dist,
            self.n_sensors,
            prey_coords,
            include_blocks=False,
            check_border=False,
            use_relative_angle=True,
        )

        pred_coords = np.array(
            [
                [s[0], s[1]]
                for i, s in enumerate(state.predator_states)
                if i != int(agent_id)
            ]
        )
        pred_obs = self.world.check_collision_circular_rays(
            pos_i,
            self.obs_dist,
            self.n_sensors,
            pred_coords,
            include_blocks=False,
            check_border=False,
            use_relative_angle=True,
        )

        obstacle_obs = self.world.check_collision_circular_rays(
            pos_i,
            self.obs_dist,
            self.n_sensors,
            other_agents=None,
            include_blocks=True,
            check_border=True,
            use_relative_angle=True,
        )

        obs = np.full((self.obs_dim,), self.obs_dist, dtype=np.float32)
        # TODO try and vectorize this
        for k in range(self.n_sensors):
            sensor_readings = [obstacle_obs[k], pred_obs[k], prey_obs[k]]
            min_val = min(sensor_readings)
            min_idx = sensor_readings.index(min_val)
            obs[min_idx * self.n_sensors + k] = min_val

        return obs

    def _get_rewards(
        self, state: PPState, next_state: PPState
    ) -> Dict[M.AgentID, float]:
        new_caught_prey = []
        for i in range(self.num_prey):
            if not state.prey_caught[i] and next_state.prey_caught[i]:
                new_caught_prey.append(next_state.prey_states[i])

        if len(new_caught_prey) == 0:
            return {i: 0.0 for i in self.possible_agents}

        if self.cooperative:
            reward = len(new_caught_prey) * (self._per_prey_reward)
            return {i: reward for i in self.possible_agents}

        rewards = {i: 0.0 for i in self.possible_agents}
        pred_states = next_state.predator_states
        for prey_state in new_caught_prey:
            pred_dists = np.linalg.norm(prey_state[:2] - pred_states[:, :2], axis=1)
            involved_predators = np.where(pred_dists <= self.COLLISION_DIST)[0]
            predator_reward = self._per_prey_reward / len(involved_predators)
            for i in involved_predators:
                rewards[str(i)] += predator_reward

        return rewards


class PPWorld(SquareContinuousWorld):
    """A continuous 2D world for the Predator-Prey Problem."""

    def __init__(
        self,
        size: int,
        blocks: Optional[List[CircleEntity]],
        predator_start_positions: Optional[List[Position]] = None,
        prey_start_positions: Optional[List[Position]] = None,
        predator_angles: Optional[List[float]] = None,
    ):
        assert size >= 3
        super().__init__(size=size, blocks=blocks, agent_radius=0.5)

        if predator_start_positions is None:
            predator_start_positions = []
            for col, row in product([0, size // 2, size - 1], repeat=2):
                if col not in (0, size - 1) and row not in (0, size - 1):
                    continue
                x, y = col + self.agent_radius, row + self.agent_radius
                invalid_pos = False
                for block_pos, block_size in self.blocks:
                    dist = self.euclidean_dist((x, y, 0.0), block_pos)
                    if dist <= self.agent_radius + block_size:
                        invalid_pos = True
                        break
                if not invalid_pos:
                    predator_start_positions.append((x, y, 0.0))

        self.predator_start_positions = predator_start_positions

        if prey_start_positions is None:
            # prey can start anywhere at least distance 2 * self.agent size away from
            # any predator (i.e. an agent wide gap from any predator)
            # Not very efficient, but only needs to be run once at the start
            prey_start_positions = []
            for col, row in product(range(size), range(size)):
                if col in (0, size - 1) or row in (0, size - 1):
                    continue
                x, y = col + self.agent_radius, row + self.agent_radius
                invalid_pos = False
                for pred_pos in self.predator_start_positions:
                    dist = self.euclidean_dist((x, y), pred_pos)
                    if dist < 2 * self.agent_radius:
                        invalid_pos = True
                        break
                if invalid_pos:
                    continue
                for block_pos, block_size in self.blocks:
                    dist = self.euclidean_dist((x, y), block_pos)
                    if dist < self.agent_radius + block_size:
                        invalid_pos = True
                        break
                if not invalid_pos:
                    prey_start_positions.append((x, y, 0.0))

        self.prey_start_positions = prey_start_positions


def parse_world_str(world_str: str) -> PPWorld:
    """Parse a str representation of a world into a world object.

    Notes on world str representation:

    . = empty/unallocated cell
    # = a block
    P = starting location for predator agents [optional] (defaults to edges)
    p = starting location for prey agent [optional] (defaults to center)

    Examples (" " quotes and newline chars omitted):

    1. A 10x10 world with 4 groups of blocks and using the default predator
       and prey start locations.

    ..........
    ..........
    ..##..##..
    ..##..##..
    ..........
    ..........
    ..##..##..
    ..##..##..
    ..........
    ..........

    2. Same as above but with predator and prey start locations defined for
    up to 8 predators and 4 prey. (This would be the default layout for the
    scenario where there are between 2 and 4 prey, i.e. if prey and predator
    start locations were left unspecified as in example 1.)

    P....P...P
    ..........
    ..##..##..
    ..##..##..
    ....pp....
    P...pp...P
    ..##..##..
    ..##..##..
    ..........
    P....P...P

    """
    row_strs = world_str.splitlines()
    assert len(row_strs) > 1
    assert all(len(row) == len(row_strs[0]) for row in row_strs)
    assert len(row_strs[0]) > 1
    assert len(row_strs) == len(row_strs[0])

    size = len(row_strs)
    blocks: Set[CircleEntity] = set()
    predator_coords = set()
    prey_coords = set()
    for r, c in product(range(size), repeat=2):
        # This is offset to the center of the square
        pos = (c + 0.5, r + 0.5, 0)
        char = row_strs[r][c]

        if char == "#":
            # Radius is 0.5
            blocks.add((pos, 0.5))
        elif char == "P":
            predator_coords.add(pos)
        elif char == "p":
            prey_coords.add(pos)
        else:
            assert char == "."

    return PPWorld(
        size,
        blocks=list(blocks),
        predator_start_positions=None
        if len(predator_coords) == 0
        else list(predator_coords),
        prey_start_positions=None if len(prey_coords) == 0 else list(prey_coords),
    )


def get_default_world(size: int, include_blocks: bool) -> PPWorld:
    """Get function for generaing default world with given size.

    If `include_blocks=True` then world will contain blocks with the following layout:

    .....
    .#.#.
    .....
    .#.#.
    .....

    Where `#` are the blocks, which will be represented as a single circle.
    """
    r = float(size / 10)
    if include_blocks:
        blocks = [
            ((x + r, y + r, 0.0), r)
            for x, y in product([size / 5, 3 * size / 5], repeat=2)
        ]
    else:
        blocks = []
    return PPWorld(size=size, blocks=blocks)


def get_5x5_world() -> PPWorld:
    """Generate 5x5 world layou`t."""
    return get_default_world(5, include_blocks=False)


def get_5x5_blocks_world() -> PPWorld:
    """Generate 5x5 Blocks world layout."""
    return get_default_world(5, include_blocks=True)


def get_10x10_world() -> PPWorld:
    """Generate 10x10 world layou`t."""
    return get_default_world(10, include_blocks=False)


def get_10x10_blocks_world() -> PPWorld:
    """Generate 10x10 Blocks world layout."""
    return get_default_world(10, include_blocks=True)


def get_15x15_world() -> PPWorld:
    """Generate 15x15 world layou`t."""
    return get_default_world(15, include_blocks=False)


def get_15x15_blocks_world() -> PPWorld:
    """Generate 15x15 Blocks world layout."""
    return get_default_world(15, include_blocks=True)


def get_20x20_world() -> PPWorld:
    """Generate 20x20 world layout."""
    return get_default_world(20, include_blocks=False)


def get_20x20_blocks_world() -> PPWorld:
    """Generate 20x20 Blocks world layout."""
    return get_default_world(20, include_blocks=True)


#  (world_make_fn, step_limit)
SUPPORTED_WORLDS = {
    "5x5": (get_5x5_world, 25),
    "5x5Blocks": (get_5x5_blocks_world, 50),
    "10x10": (get_10x10_world, 50),
    "10x10Blocks": (get_10x10_blocks_world, 50),
    "15x15": (get_15x15_world, 100),
    "15x15Blocks": (get_15x15_blocks_world, 100),
    "20x20": (get_20x20_world, 200),
    "20x20Blocks": (get_20x20_blocks_world, 200),
}
