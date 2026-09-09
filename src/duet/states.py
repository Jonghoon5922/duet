"""상태 값과 시간 규칙. 문자열 리터럴을 코드 곳곳에 흩지 않는다."""

from __future__ import annotations

from datetime import datetime

#: 하트비트 주기와 죽음 판정 기준 (SPEC 4절).
HEARTBEAT_SEC = 30.0
STALE_SEC = 90.0

TASK_WAITING = "대기"
TASK_RUNNING = "진행중"
TASK_DONE = "완료"
TASK_STOPPED = "중지"
TASK_CANCELLED = "취소"
TASK_STATUSES = (TASK_WAITING, TASK_RUNNING, TASK_DONE, TASK_STOPPED, TASK_CANCELLED)

#: 아직 어떤 타스크에도 붙지 않은 세션. 타스크 계산에 끼지 않는다.
SESSION_IDLE = "미참여"
SESSION_RUNNING = "진행중"
SESSION_DONE = "완료"
SESSION_STOPPED = "중지"
SESSION_STATUSES = (SESSION_IDLE, SESSION_RUNNING, SESSION_DONE, SESSION_STOPPED)

#: 더 이상 하트비트를 기대하지 않는 세션 상태.
SESSION_CLOSED = (SESSION_DONE, SESSION_STOPPED)

ACTOR_SESSION = "세션"
ACTOR_HUMAN = "사람"
ACTOR_AUTO = "자동"


def now() -> str:
    """파일에 적는 시각. 로컬 ISO — 사람이 읽고 정렬도 되는 꼴.

    밀리초까지 적는다. 초까지만 적으면 같은 초에 벌어진 일들의 순서가 사라져서
    "마지막 활동순" 정렬이 흔들린다.
    """
    return datetime.now().isoformat(timespec="milliseconds")


def age_seconds(stamp: str) -> float:
    """그 시각에서 지금까지 몇 초. 읽을 수 없는 값이면 아주 오래된 것으로 친다.

    사람이 파일을 손으로 고치다 시각을 망가뜨릴 수 있다. 그때 '살아있음'으로 읽으면
    죽은 세션이 영원히 타스크를 잡아 둔다. 모르면 죽은 쪽으로 센다.
    """
    try:
        return (datetime.now() - datetime.fromisoformat(stamp)).total_seconds()
    except (TypeError, ValueError):
        return float("inf")
