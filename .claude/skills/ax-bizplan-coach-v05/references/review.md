# 평가표와 개정의 순서

제조AI에서는 제조 공정·AI 데이터·스마트공장 사업·공급 PM·정산 담당 5개 관점을 적용한다. 다른 제도는 personas-and-rules.md의 역할을 적용한다. 기준표 ID, 항목별 정수 만점, 판정 척도를 초안 전에 기록한다. 교육용 점수와 해당 회차 공식 점수를 구별한다.

항목의 질문→원고의 절/표→가능한 의사결정→결손→설계점수→외부 증빙 상태를 한 행으로 기록한다. 정수점수의 공통 기준은 누락 0, 관련 주장만 있으면 만점의 1/4 이내, 대상·책임·계산 중 일부 확인은 1/2 수준, 자료와 절차로 설계를 따라갈 수 있으나 결정 조건이 하나 남으면 3/4 수준, 필수 판단 조건을 모두 추적할 수 있으면 만점이다. 가장 가까운 정수로 표시하되 점수와 이유를 같이 남긴다. 현재 실적을 묻는 항목은 계획만으로 만점을 줄 수 없다. 설계가 질문인 항목은 미래 실적 부재로 이중 감점하지 않는다. 같은 결손에는 같은 이슈 ID를 부여한다. 동점이면 의사결정·증빙의 선행조건, 만점, 항목 순으로 선정한다. 평균·합계는 프로그램으로 다시 계산한다.

준비도는 점수와 별도다. 자료별 ‘원본 확인/합성 자료/확인 요청/충돌/시운전 후 판정/조건부 제외’를 유지한다. 필수 미수령·충돌·서식 미고정·미승인이 하나라도 있으면 제출 준비 상태의 이유와 담당자를 표시한다. 충족은 해당 조건을 검사한 결과일 뿐 행정기관의 수리나 선정 판단이 아니다. 공급사 미확인에서 문장만 길어진 경우 증빙 상태는 동일해야 한다.

버전당 완성본 하나를 저장한다. v1 평가를 본 뒤 처음으로 v2를 쓰고, v2 평가와 반대 심사를 본 뒤 처음으로 v3를 쓴다. 교육형은 분량을 새로 계산하고 동일 의미의 문장을 줄인다. 제출형은 서식의 분량 기준·표별 한도를 따른다. 개정 기록에는 바꾼 장, 변경된 결정, 새 증거, 계속 남은 근거, 연쇄 참조를 쓴다. 현장 담당자의 역할·사업기간·비용을 소급해 입력에 추가하지 않는다.

### 실행 로그

새 출력 디렉터리에서 다음을 실행한다. 명령의 INPUT·OUT은 실제 경로로 치환한다. prompt.txt에는 재구성한 예시 대신 실제 실행 요청을 쓴다. 실제 사용자가 지정한 데이터 범위와 추가 가정을 구분한다.

```sh
python3 SKILL/scripts/runlog.py init OUT --prompt prompt.txt --model MODEL --inputs INPUT1 INPUT2 --depth full --purpose submission
python3 SKILL/scripts/runlog.py add OUT draft1 v1.md
python3 SKILL/scripts/runlog.py add OUT review1 review1.md
python3 SKILL/scripts/runlog.py add OUT draft2 v2.md
python3 SKILL/scripts/runlog.py add OUT review2 review2.md adversarial.md
python3 SKILL/scripts/runlog.py add OUT draft3 v3.md
python3 SKILL/scripts/runlog.py add OUT final final.docx comparison.md
python3 SKILL/scripts/runlog.py verify OUT
```

간단 모드는 init 시 --depth simple, draft2 다음 final이다. 기록에는 입력·스킬 해시, UTC 시각, 당시 파일 사본, 실행 순서를 보존한다. 중단 후 verify하고 마지막으로 기록된 단계 다음부터 재개한다. 이미 기록한 결과를 바꾸려면 새 실행 디렉터리를 사용한다. 로컬 해시는 내용 대조용이며 타임스탬프 인증을 대신하지 않는다.
