# Duet

나와 Claude가 함께 하는 일을 타스크로 묶고, 세션이 다 끝나면 타스크가 닫히는 **개인용 로컬 PMS**.
세션은 사람이 아니라 Claude가 열고 닫는다 — **MCP 서버 프로세스 하나 = 세션 하나.**

지금은 뼈대만 있는 프로토타입이다. 설계는 [SPEC.md](SPEC.md)에 있다.

## 저장

```
~/.duet/
├── T001-pc101pm-전환/
│   ├── task.md            제목·설명. 사람이 열어 고치는 파일
│   ├── s-a1b2c3d4.json    세션 A (그 프로세스만 쓴다)
│   └── s-e5f6a7b8.json    세션 B
└── _미참여/
    └── s-9f8e7d6c.json    아직 join 안 한 세션
```

DB가 없다. 한 파일에 두 주인이 없어서 락도 없다 — 세션 파일은 그 세션의 프로세스만 쓰고,
`task.md`는 사람만 쓴다. 세션이 참여하거나 보고할 때 `task.md`는 건드리지 않는다.

## 규칙 두 개

1. **타스크 상태는 저장하지 않고 센다.** 폴더 안 세션 파일을 세서 정한다.
   전부 `완료`면 `완료`, 하나라도 살아 있으면 `진행중`, `중지`가 섞이면 열어 둔 채 경고한다
   (실패한 세션을 완료로 뭉개지 않는다).
2. **사람이 정한 것이 이긴다.** `task.md`에 `상태: 완료` 한 줄을 넣으면 그게 상태다.
   그 줄을 지우면 다시 센다.

세션은 30초마다 하트비트를 찍는다. 창을 닫으면 종료 훅이 `중지`로 적고,
훅조차 못 돌면(크래시·강제 종료) 하트비트 90초 초과를 읽는 쪽이 잡는다.

## 대시보드

```bash
duet ui        # http://127.0.0.1:8737
```

왼쪽에 타스크 카드(상태 배지·세션 점·마지막 활동), 오른쪽에 세션과 진행 로그 타임라인.
요청이 올 때마다 폴더를 다시 읽으므로, 탐색기에서 `task.md`를 고쳐도 2초 안에 화면에 뜬다.
드롭다운으로 상태를 정하면 그 `상태:` 줄을 대신 써 준다 — 화면과 파일이 같은 것을 본다.

파일 감시(watchdog) + SSE는 아직이다. 지금은 2초마다 다시 물어본다.

## 쓰기

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

MCP 도구: `list_tasks` · `create_task` · `join_task` · `get_task` · `report_progress` · `complete_session` · `pause_session`

`get_task`가 인수인계다 — 세션 B가 A의 진행 로그를 읽고 이어받는다.
`pause_session`은 "여기까지"일 때. 중지된 세션이 있으면 타스크는 닫히지 않는다.
CLI: `duet serve` · `duet ui` · `duet add` · `duet list` · `duet show` · `duet version`

```bash
uv run pytest && uv run python scripts/smoke.py
```

`smoke.py`는 진짜 stdio 서버 프로세스를 여러 개 띄워 한 바퀴 돌린다.
단위 테스트가 못 보는 것(프로세스가 죽을 때 무슨 일이 벌어지는가)을 본다.

## 라이선스

MIT
