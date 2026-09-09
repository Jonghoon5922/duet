# Duet

나와 Claude가 함께 하는 일을 타스크로 묶고, 세션이 다 끝나면 타스크가 닫히는 **개인용 로컬 PMS**.
MCP로 세션이 스스로 상태를 보고하고, 사람은 대시보드에서 보고 고친다.

- 세션이 1급 객체다. **MCP 서버 프로세스 하나 = 세션 하나.**
- 이 도구는 LLM을 호출하지 않는다. 진행 로그도 Claude가 직접 쓴다.
- **DB 없음.** `~/.duet/` 아래 폴더와 파일뿐이라 탐색기로 열어 그냥 읽고 고칠 수 있다.
- 개인 전용. 네트워크 없음.

설계는 [SPEC.md](SPEC.md)에 있다.

## 지금 되는 것 (1단계)

MCP 도구: `list_tasks` · `create_task` · `join_task` · `report_progress` · `complete_session`
CLI: `duet serve` · `duet list` · `duet show` · `duet add` · `duet set` · `duet unlock` · `duet sessions`

## 붙이기

```json
{
  "mcpServers": {
    "duet": {
      "command": "uv",
      "args": ["run", "duet", "serve"],
      "cwd": "C:/project/duet"
    }
  }
}
```

세션 창을 열면 그 프로세스가 `미참여` 세션 파일을 만들고 30초마다 하트비트를 찍는다.
창을 닫으면 종료 훅이 `중지`로 적고, 훅조차 못 돌면 하트비트 90초 초과를 읽는 쪽이 잡는다.

## 저장

```
~/.duet/
├── tasks/
│   └── T001-pc101pm-전환/
│       ├── task.md              ← 정의·설명·이력. 사람과 update_task만 쓴다
│       └── sessions/
│           ├── s-a1b2c3d4.json  ← 세션 A의 프로세스만 쓴다
│           └── s-e5f6a7b8.json  ← 세션 B의 프로세스만 쓴다
└── unassigned/
    └── s-9f8e7d6c.json          ← 아직 join 안 한 세션
```

**한 파일에 두 주인이 없어서 충돌이 없다.** 세션 파일은 그 세션의 프로세스만 쓰고,
`task.md`는 사람 쪽만 쓴다. 세션이 참여하거나 보고할 때 `task.md`는 건드리지 않는다.

## 상태

| 대상 | 상태 |
|---|---|
| 타스크 | `대기` → `진행중` → `완료` / `중지` / `취소` |
| 세션 | `미참여` → `진행중` → `완료` / `중지` |

**타스크 상태는 저장하지 않고 계산한다.** 세션 파일들을 읽어서 정한다:
세션이 1개 이상이고 전부 `완료`일 때만 `완료`. `중지`가 하나라도 있으면 열어 둔 채로 사람이 판단한다.

사람이 정한 상태만 `task.md`의 `status_override`에 남고, 그것이 계산값을 덮는다 (= 수동 잠금).
`duet unlock <id>` 로 풀면 다시 계산값으로 돌아간다.

## 라이선스

MIT
