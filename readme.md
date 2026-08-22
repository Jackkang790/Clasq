# 프리셋 불러오기 기능 수정 지침

## 1. 작업 목적

기존 `organize_view`에 추가된 **「프리셋 불러오기」 기능의 데이터 소스를 변경한다.**

기존에 `file_manager.db`의 `managed_paths`를 조회하는 방식으로 구현되어 있다면 해당 방식을 사용하지 않는다.

### 변경 사항

```text
기존
[프리셋 불러오기]
      ↓
file_manager.db
      ↓
managed_paths 조회
      ↓
프리셋 표시
```

```text
변경 후
[프리셋 불러오기]
      ↓
assets/preset.json
      ↓
presets 배열 확인
      ↓
preset_name을 드롭다운에 표시
```

**DB에 접속하여 프리셋을 가져오지 않는다.**

---

# 2. 프리셋 파일 위치

프리셋 데이터는 다음 JSON 파일에서 불러온다.

```text
assets/preset.json
```

프로젝트의 기존 경로 관리 방식을 확인하여 실행 위치에 관계없이 정상적으로 `assets/preset.json`을 찾을 수 있도록 한다.

---

# 3. preset.json 구조

`preset.json`은 다음과 같은 구조를 가진다.

```json
{
    "presets": [
        {
            "preset_name": "1",
            "targets": [
                {
                    "name": "새 폴더",
                    "path": "D:\\새 폴더",
                    "type": "folder",
                    "extensions": [
                        ".gif",
                        ".hwpx",
                        ".jpg",
                        ".png",
                        ".yaml",
                        ".yml"
                    ]
                }
            ],
            "extensions": [
                ".gif",
                ".hwpx",
                ".jpg",
                ".png",
                ".yaml",
                ".yml"
            ]
        },
        {
            "preset_name": "테스트",
            "targets": [
                {
                    "name": "3_블로그_시스템 설계서 - 복사본.hwpx",
                    "path": "D:\\새 폴더\\3_블로그_시스템 설계서 - 복사본.hwpx",
                    "type": "file",
                    "extensions": [
                        ".hwpx"
                    ]
                },
                {
                    "name": "application2.yml",
                    "path": "D:\\새 폴더\\application2.yml",
                    "type": "file",
                    "extensions": [
                        ".yml"
                    ]
                },
                {
                    "name": "application3.yaml",
                    "path": "D:\\새 폴더\\application3.yaml",
                    "type": "file",
                    "extensions": [
                        ".yaml"
                    ]
                }
            ],
            "extensions": [
                ".gif",
                ".hwpx",
                ".jpg",
                ".png",
                ".yaml",
                ".yml"
            ]
        }
    ]
}
```

---

# 4. 프리셋 이름 표시

JSON의 다음 속성을 사용한다.

```text
preset_name
```

예를 들어:

```json
{
    "preset_name": "1"
}
```

```json
{
    "preset_name": "테스트"
}
```

라면 프리셋 불러오기 UI의 드롭다운에는 다음과 같이 표시한다.

```text
┌─────────────────────────────┐
│ 프리셋을 선택하세요       ▼ │
├─────────────────────────────┤
│ 1                           │
│ 테스트                      │
└─────────────────────────────┘
```

즉, **`targets`의 `name`이나 `path`를 드롭다운 이름으로 사용하지 않는다.**

반드시:

```text
presets[*].preset_name
```

을 표시한다.

---

# 5. 프리셋 불러오기 동작

`organize_view`의 **「프리셋 불러오기」 버튼을 클릭하면** 다음 순서로 동작한다.

```text
[프리셋 불러오기]
        ↓
assets/preset.json 확인
        ↓
파일 존재
   ├─ YES
   │    ↓
   │  JSON 읽기
   │    ↓
   │  "presets" 배열 확인
   │    ↓
   │  preset_name 추출
   │    ↓
   │  드롭다운에 표시
   │
   └─ NO
        ↓
      "프리셋 없음" 표시
```

---

# 6. 프리셋 선택

사용자가 드롭다운에서 특정 `preset_name`을 선택하면 해당 프리셋의 전체 데이터를 가져온다.

예:

```text
{
    "preset_name": "테스트",
    "targets": [
        ...
    ],
    "extensions": [
        ...
    ]
}
```

이 중 선택된 프리셋의 `targets` 정보를 기존 `organize_view`의 정리 대상 데이터에 적용한다.

### 중요

단순히 `preset_name`만 화면에 표시하고 끝내는 것이 아니다.

사용자가 프리셋을 선택하면 해당 프리셋의:

```text
targets
extensions
```

등 기존 정리 기능에서 필요한 데이터를 정상적으로 전달해야 한다.

---

# 7. targets 데이터 처리

각 `targets` 항목은 다음 구조를 가진다.

```json
{
    "name": "새 폴더",
    "path": "D:\\새 폴더",
    "type": "folder",
    "extensions": [
        ".gif",
        ".hwpx",
        ".jpg"
    ]
}
```

또는:

```json
{
    "name": "application2.yml",
    "path": "D:\\새 폴더\\application2.yml",
    "type": "file",
    "extensions": [
        ".yml"
    ]
}
```

기존 `organize_view`에서 사용하는 데이터 구조가 있다면 그 구조에 맞춰 변환하여 적용한다.

### type

```text
type = "folder"
→ 폴더 대상

type = "file"
→ 파일 대상
```

### path

실제 대상 경로로 사용한다.

### extensions

해당 대상에 적용되는 확장자 목록으로 사용한다.

---

# 8. preset.json이 존재하지 않는 경우

`assets/preset.json` 파일이 존재하지 않는 경우 프로그램이 오류로 종료되어서는 안 된다.

다음과 같이 처리한다.

```text
assets/preset.json 없음
        ↓
예외 발생하지 않음
        ↓
"프리셋 없음" 표시
```

UI에서는 사용자가 프리셋이 없다는 것을 명확하게 알 수 있도록 한다.

예:

```text
┌─────────────────────────────┐
│ 프리셋 없음                  │
└─────────────────────────────┘
```

또는 기존 프로젝트의 UI 디자인에 맞춰:

```text
[프리셋 없음 ▼]
```

형태로 표시한다.

**기존 UI 스타일을 우선한다.**

---

# 9. presets가 비어 있는 경우

파일은 존재하지만 다음과 같이 `presets`가 비어 있는 경우도 고려한다.

```json
{
    "presets": []
}
```

이 경우에도:

```text
프리셋 없음
```

을 표시한다.

---

# 10. JSON 오류 처리

`preset.json`이 존재하지만 JSON 형식이 잘못된 경우에도 프로그램이 종료되어서는 안 된다.

예:

```text
preset.json 존재
      ↓
JSON 파싱 실패
      ↓
예외 처리
      ↓
프리셋 없음 표시
```

가능하다면 사용자에게 다음과 같이 알릴 수 있다.

```text
프리셋을 불러올 수 없습니다.
preset.json 파일을 확인해주세요.
```

단, 기존 프로그램의 오류 표시 방식이 있다면 해당 방식을 우선 사용한다.

---

# 11. DB 사용 금지

이번 기능에서는 **프리셋 정보를 가져오기 위해 `file_manager.db`에 접근하지 않는다.**

다음과 같은 기존 로직이 있다면 제거하거나 사용하지 않도록 수정한다.

```text
file_manager.db
    ↓
managed_paths
    ↓
프리셋 목록
```

반드시 다음 구조로 변경한다.

```text
assets/preset.json
    ↓
presets
    ↓
preset_name
    ↓
드롭다운
```

`managed_paths`는 이번 프리셋 불러오기 기능의 데이터 소스로 사용하지 않는다.

---

# 12. 기존 UI 유지

이번 수정은 **프리셋 데이터의 저장/조회 방식만 변경하는 것**을 기본으로 한다.

기존 `organize_view`의 레이아웃과 디자인을 불필요하게 변경하지 않는다.

기존 버튼:

```text
[프리셋 불러오기] [경로 추가하기] [자동정리하기]
```

의 배치도 유지한다.

`프리셋 불러오기` 버튼의 디자인 역시 기존 `경로 추가하기` 버튼과 동일한 스타일을 유지한다.

---

# 13. 최종 동작 예시

`assets/preset.json`에 다음 데이터가 있다고 가정한다.

```json
{
    "presets": [
        {
            "preset_name": "1",
            "targets": []
        },
        {
            "preset_name": "테스트",
            "targets": []
        }
    ]
}
```

사용자가 `organize_view`에서:

```text
[프리셋 불러오기]
```

버튼을 클릭하면:

```text
┌─────────────────────────────┐
│ 프리셋 선택              ▼  │
├─────────────────────────────┤
│ 1                           │
│ 테스트                      │
└─────────────────────────────┘
```

처럼 표시한다.

`테스트`를 선택하면:

```text
presets[1]
```

에 해당하는 프리셋 데이터를 가져와 기존 `organize_view` 정리 대상에 적용한다.

---

# 14. 최종 체크리스트

* [ ] 프리셋 데이터는 `assets/preset.json`에서 가져오는가?
* [ ] `file_manager.db`에 접속하지 않는가?
* [ ] `managed_paths`를 사용하지 않는가?
* [ ] JSON의 최상위 `presets` 배열을 읽는가?
* [ ] `preset_name`을 드롭다운에 표시하는가?
* [ ] `targets` 데이터를 선택한 프리셋의 데이터로 사용하는가?
* [ ] `targets[].name`을 프리셋 이름으로 잘못 사용하지 않는가?
* [ ] `targets[].path`를 정상적으로 처리하는가?
* [ ] `targets[].type`을 정상적으로 처리하는가?
* [ ] `targets[].extensions`를 정상적으로 처리하는가?
* [ ] `preset.json`이 없을 경우 `프리셋 없음`을 표시하는가?
* [ ] `presets` 배열이 비어 있을 경우 `프리셋 없음`을 표시하는가?
* [ ] JSON 파싱 오류가 발생해도 프로그램이 종료되지 않는가?
* [ ] 기존 `organize_view` UI를 유지하는가?
* [ ] 기존 `[프리셋 불러오기] [경로 추가하기] [자동정리하기]` 버튼 배치를 유지하는가?
