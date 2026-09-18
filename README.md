# Netrunner 번역 보조 — 번역가용 사용 설명서

Android: Netrunner 카드를 EN→KO로 옮길 때, **용어·과거 번역·문형·검수**를 물어볼 수 있는
MCP 도구 4개입니다. 번역기를 대신하는 물건이 아니라, 옆에 두고 조회하는 사전에 가깝습니다.

기획 사양 전체는 [SERVICE.md](SERVICE.md), 구현 배선과 기술 세트는 [STACK.md](STACK.md)에
있습니다. 이 문서는 **번역가가 실제로 할 수 있는 것만** 다룹니다.

## 지금 되는 것 / 아직 안 되는 것

| | 상태 |
|---|---|
| MCP 도구 4개 (용어 조회·TM 검색·문형 조회·검수) | **사용 가능** |
| 검토 큐 (초벌을 받아 승인·수정) | 미구현 — 배선만 있고 사람이 들어갈 입구가 없습니다 |
| REST API (`GET /review/queue` 등) | 미구현 |
| 용어 확정 대기 목록 (읽기 전용 뷰) | **볼 수 있음** — 아래 참조 |
| GitHub Issues 용어 확정 절차 | 부분 가동 — Issue 12건이 열려 있고 자동 동기화는 미구현 |

즉 **초벌 번역을 받아보는 워크플로는 아직 없습니다.** 지금 쓸 수 있는 것은 내가 쓴 번역을
검증하고, 과거에 팀이 뭐라고 옮겼는지 찾아보는 용도입니다.

**용어 확정 대기 목록**은 <https://castigar.github.io/netrunner-glossary/> 에서 볼 수 있습니다.
읽기 전용이라 여기서 확정하지는 못하고, 충돌 목록과 항목별 Issue 링크만 봅니다. 한계 두 가지 —
룰 경로 항목 13건은 근거 카드가 비어 Issue를 아직 만들지 않았고(그 행의 "Issue 열기"는 빈 검색
결과로 갑니다), 신규 용어 후보 표는 발행 경로가 없어 항상 비어 있습니다.

## 준비물

1. **이 저장소** 체크아웃
2. **코퍼스** — `netrunner-cards-json` 체크아웃 (TM 검색에만 필요)
3. **자산 파일 3개** — `assets/glossary.json`, `assets/patterns.json`, `assets/conflicts.json`
   (없으면 용어·문형 도구가 전부 "없음"으로 답합니다. `src/build_assets.py`로 생성합니다)
4. **Python 3.11+** 와 의존성: `uv sync`

## MCP 클라이언트에 붙이기

서버는 stdio 모드로 뜹니다. 클라이언트 설정에 이렇게 넣습니다:

```json
{
  "mcpServers": {
    "netrunner-translation": {
      "command": "<파이썬 실행 파일 절대경로>",
      "args": ["-m", "mcp_server"],
      "env": {
        "PYTHONPATH": "<이 저장소>/src",
        "CORPUS_ROOT": "<netrunner-cards-json 체크아웃 경로>"
      }
    }
  }
}
```

| 환경변수 | 쓰임 |
|---|---|
| `PYTHONPATH` | `src`를 가리켜야 합니다. 절대경로면 작업 디렉터리와 무관하게 동작합니다 |
| `CORPUS_ROOT` | `search_tm` 전용. 없으면 나머지 3개는 정상, TM 검색만 에러 JSON을 돌려줍니다 |
| `GLOSSARY_PATH` / `PATTERNS_PATH` | 선택. 기본값은 `assets/` 아래이고, 이 경로는 모듈 위치 기준이라 따로 안 줘도 됩니다 |

붙기 전에 한 번 확인하려면:

```bash
PYTHONPATH=src python -c "import mcp_server; print(mcp_server.lookup_term('trash'))"
# {"found": true, "en": "trash", "ko": "폐기", "source": "extracted", "llm_judged": true}
```

## 도구 4개

### 1. `lookup_term(en)` — 이 용어, 우리는 뭐라고 쓰나

```
lookup_term("install")
→ {"found": true, "en": "install", "ko": "설치", "source": "extracted", "llm_judged": true}

lookup_term("Barrier")
→ {"found": true, "en": "Barrier", "ko": "방벽", "source": "official", "llm_judged": true}

lookup_term("zzzz")
→ {"found": false, "en": "zzzz"}
```

대소문자를 가리지 않고, 밑줄은 공백으로 봅니다(`end_the_run` = `end the run`).

`source`를 꼭 보세요:

- `official` — 게임 공식 용어집(진영·타입·부제 등)에서 왔습니다. 그대로 씁니다.
- `subtype_extracted` / `extracted` — **과거 번역물에서 통계로 뽑은 것**입니다. 대체로 맞지만
  노이즈가 섞여 있습니다. 확신이 안 서면 `search_tm`으로 실제 용례를 확인하세요.

`found: false`는 "이 용어는 아직 팀 기준이 없다"는 뜻입니다. 새로 정하는 역어라면 동료와
합의가 필요한 자리입니다.

### 2. `search_tm(text)` — 비슷한 카드를 과거에 어떻게 옮겼나

```
search_tm("Trash 1 installed program.")
→ [
    {
      "id": "rototurret",
      "en_text": "[subroutine] Trash 1 installed program.\n[subroutine] End the run.",
      "ko_text": "[subroutine] 프로그램 1개를 폐기한다.\n[subroutine] 런을 종료한다",
      "score": 0.0484,
      "bm25_rank": 1, "char_rank": 1, "dense_rank": 1
    },
    ... 최대 5건
  ]
```

BM25(정확한 단어) + 임베딩(의미) + 문자 n-gram(정형구) 세 신호를 합쳐 정렬합니다.
세 랭크가 모두 높은 결과가 가장 믿을 만합니다.

- **첫 호출은 수십 초 걸립니다.** 임베딩 모델을 내려받고 코퍼스를 색인합니다. 이후는 빠릅니다.
- 색인 대상은 학습 분할뿐입니다. 평가용 hold-out 카드는 검색되지 않습니다.
- `CORPUS_ROOT`가 없으면 `{"error": "TM index unavailable", ...}`가 돌아옵니다.

### 3. `lookup_pattern(text)` — 정형 문장의 표준 문형

```
lookup_pattern("[subroutine] End the run.")
→ {"found": true, "en_template": "{SYM} End the run.",
   "ko_template": "{SYM} 런을 종료한다.", "count": 44,
   "examples": ["archer", "enigma", "hadrians_wall"]}

lookup_pattern("2[credit]: +1 strength.")
→ {"found": true, "en_template": "{N}{SYM}: +{N} strength.",
   "ko_template": "{N}{SYM}: 힘을 +{N} 한다.", "count": 30, ...}
```

숫자는 `{N}`, 게임 기호는 `{SYM}`으로 치환해 맞춥니다. `count`는 코퍼스에서 그 문형이 나온
횟수라 곧 그 표현의 관례적 무게입니다. `found: false`면 정형 문장이 아니라는 뜻이니
`search_tm`으로 넘어가세요.

### 4. `check_translation(en, ko)` — 초벌 자가 검수

```
check_translation("Install a program.", "프로그램을 설치한다.")
→ {"passed": true,
   "injection": {"passed": true, "matches": []},
   "glossary": {"passed": true, "violations": [], "llm_judged": true},
   "fidelity": {"passed": true, "violations": []}}

check_translation("Gain 2[credit].", "크레딧 3을 얻는다.")
→ fidelity 위반: 숫자 2가 KO에 없고 3이 추가됨 + [credit] 기호 누락
```

세 가드를 돌립니다:

| 가드 | 보는 것 |
|---|---|
| `injection` | 카드 원문에 프롬프트 주입 문구가 섞였는지 |
| `glossary` | EN 원문에 있는 등재 용어의 등재 역어가 KO에 들어 있는지 |
| `fidelity` | 숫자·게임 기호·조건 구조가 EN과 KO에서 일치하는지 |

`passed`는 **셋 다 통과해야** `true`입니다.

## 결과를 어디까지 믿을 것인가

**용어 가드는 문자열 부분일치입니다.** 등재 역어가 KO 문장 안에 그 형태 그대로 들어 있는지만
봅니다(정규화하는 것은 괄호 앞뒤 공백뿐). 그래서 조사·활용이 붙으면 오탐이 납니다:

```
check_translation("[subroutine] End the run.", "[subroutine] 런을 종료한다.")
→ 위반: end the run → 기대 "런 종료"
```

번역은 맞지만 등재형이 `런 종료`라 걸린 것입니다. **`extracted` 출처 위반은 대체로 이런
오탐입니다.** 실측 예를 하나 더 들면, `Trash the top card of R&D.` / `R&D 상단 카드를
폐기한다.`는 `top → 위 카드` 위반이 뜹니다 — 용어집 쪽 품질 문제입니다.

그러니 `check_translation`의 위반은 **확정 오류가 아니라 확인해볼 지점**으로 읽으세요.
반대로 `fidelity` 위반(숫자·기호 불일치)은 거의 항상 진짜 오류입니다. `[credit]` `[mu]`
같은 기호는 KO 번역문에도 그대로 남겨야 합니다.

## 문제가 생기면

| 증상 | 원인 | 조치 |
|---|---|---|
| `{"error": "TM index unavailable"}` | `CORPUS_ROOT` 미설정 또는 코퍼스 로드 실패 | 경로가 `netrunner-cards-json` 체크아웃 루트를 가리키는지 확인 |
| 첫 `search_tm`이 한참 멈춤 | 임베딩 모델 로드 + 색인 구축 | 정상입니다. 한 번만 겪습니다 |
| 모든 `lookup_term`이 `found: false` | `assets/glossary.json` 없음 | `src/build_assets.py`로 자산을 먼저 만드세요 |
| 용어가 있는데 위반으로 뜸 | 조사·활용 때문에 부분일치 실패 | 위 "어디까지 믿을 것인가" 참고. 오탐입니다 |

## 검증 상태

`tests/test_mcp_server.py` — **23개 전부 통과** (실측).

| 도구 | 케이스 | 내용 |
|---|---|---|
| `lookup_term` | 7 | 등재어 조회, 미등재어, 대소문자·밑줄 정규화, JSON 유효성, `llm_judged` |
| `search_tm` | 5 | 코퍼스 부재 시 에러 JSON, 필수 필드, score 내림차순, 상위 결과 |
| `lookup_pattern` | 4 | JSON 유효성, 미매칭, 템플릿 필드, `found` 타입 |
| `check_translation` | 7 | 3개 가드 섹션, 정상 통과, 용어·주입·기호 위반 검출 |

알려진 공백 두 가지:

- 자산 파일이 없는 환경에서는 18개가 **조용히 스킵**되고 `search_tm` 5개만 남습니다.
- 테스트는 함수를 파이썬으로 직접 호출합니다. **MCP 등록·stdio 서버 자체는 검증 범위 밖**이라,
  도구를 등록 목록에서 빼도 테스트는 통과합니다.

## 개발자 노트

도구는 `@mcp.tool()` 데코레이터가 아니라 파일 하단에서 `mcp.tool()(fn)`으로 등록합니다.
데코레이터를 쓰면 모듈 레벨 이름이 `FunctionTool` 객체로 재바인딩돼 `mcp_server.lookup_term(...)`
직접 호출이 깨지기 때문입니다. **새 도구는 그 목록에 추가하세요.**
