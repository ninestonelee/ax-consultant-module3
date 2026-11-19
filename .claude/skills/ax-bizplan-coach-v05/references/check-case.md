# 원장·목표·예산 검산

제조AI 과제에 생산수·독립 최초불량수·설비군 사용전력(또는 추정전력)이 모두 있을 때 적용한다. 서로 다른 주기의 원장, 검사 미완료 수량, 전력 범위가 섞인 입력은 현장 담당자와 기간·설비·로트 정의를 맞춘 뒤 사용한다. 한 행이 한 로트의 최종 마감 수량인 CSV를 전제로 한다. 연속 센서·개별 검사 원시 데이터에는 이 로트 진단을 적용하지 않는다. 지표가 다른 과제에서는 대응하는 산식을 별도로 명시하고 이 예제의 없는 열을 0으로 채우지 않는다.

실제 입력에서 해당하는 값만 옮겨 assets/neutral-case.json을 실행 폴더의 case.json으로 복사·수정한다. 예시의 설비·금액은 신청 정보가 아니다. 스킬 디렉터리를 기준으로 아래처럼 호출한다.

```sh
python3 scripts/check_case.py --spec /path/to/case.json --csv /path/to/lots.csv --out /path/to/checks.json --html /path/to/지표근거.html
```

필수 필드는 source(원장 자료 ID), columns(원본 CSV의 열 이름), quality_boundary(불량 계수 공정·분모 범위), energy(kind, boundary, source)이다. 기간이 월 단위면 period_unit을 month로, 아니면 실제 단위를 쓴다. 데이터는 UTF-8 CSV이며 BOM도 허용한다. 빈 LOT, 중복 LOT, 음수, 불량>생산, 정격×등가시간과 추정전력 불일치는 입력 오류로 종료한다. 오류 상태에서 기존 결과 파일을 재사용하지 않는다. 같은 LOT의 분할·재검 데이터는 먼저 조정 이력과 근거를 남기고 중복을 제거한다. 월 평균은 기간별 생산을 합산한 뒤 기간 수로 나눈 값이며, 계절적 변동이나 누락된 달을 보정하지 않는다.

| 설정 | 내용 |
|---|---|
| columns | lot(유일 ID), period(기간), units(생산 개수), defects(최초 불량 개수), kwh(같은 설비 범위 전력량). 선택 power_kw와 hours는 쌍으로 지정한다. 선택 energy_boundary와 energy_kind를 연결하면 모든 행이 지정 경계·실측/추정 구분과 일치해야 한다. |
| energy | kind는 estimated 또는 measured. 원천 측정이라고 입력해도 도구가 계량기·검교정을 인증하지는 않는다. 실제 계기 ID·교정 자료·책임자는 증빙 대장에 별도로 연결한다. |
| targets | defect_rate(0~1), energy_intensity(kWh/개) 각각에 {value,source,boundary} 후보를 배열로 등록한다. 서로 다른 값·경계이면 상태 conflict, 단일 계산용 목표는 null이다. 원장 경계와 목표 경계 간 불일치도 차단한다. |
| approvals | 상충을 해결한 뒤 지표별 {source,by,date,basis}로 선택한 출처·담당·ISO 날짜·결재 근거를 넣는다. 원래 후보와 충돌 내역은 보존한다. 담당자 서명/권한의 진위는 별도 검토한다. |
| energy.action | 선택 필드. 같은 기간당 대기부하 idle_kw, 조치 가능한 시간 idle_hours_per_period, 회피 가능 비율 recoverable_fraction(0~1), boundary, measured_source를 함께 입력한다. ‘감축 가능’ 산식의 한 근거이며 달성 실적이 아니다. 추정 기준선을 쓰면 여전히 실측 전 조건부로 남는다. |
| funding | total_won, public_won, cash_won, in_kind_won, source. 모든 금액은 원. 지원 한도·회차 적격성은 이 합계 검산으로 판단하지 않는다. |
| expenditure | quantities에 수량의 유일한 기준({식별자:수량})을 둔다. lines마다 id, module, kind, source, vat_won을 둔다. 구매(kind=purchase)는 qty_ref와 unit_won, 인력(kind=labor)은 monthly_won·months·fraction(0~1)을 넣는다. source는 근거 식별자이며 견적으로 확인하면 evidence_kind=quote, 급여·인력근거로 확인하면 payroll, 가정 견적은 estimate로 둔다. unknown 또는 미기입은 증빙 미확인이다. tax_basis=net(기본값) 또는 gross로 비교 기준을 명시하며 매입세액 적격 여부는 공고로 결정한다. |
| benefits | defect_won, kwh_won, excludes_energy(true·false 또는 null), source, added_opex_per_period_won, realization(0~1 시나리오 비율들). 불량 회피원가의 전력비 제외가 확인되어야 합산한다. 운영비가 비어 있으면 순편익은 null이다. |

checks.json의 trace에 입력 SHA-256과 CSV 행번호·로트·기간을 남긴다. metrics의 분자·분모와 단위, targets의 모든 후보, budget의 재원/소요, diagnostics의 미결 항목을 문서의 숫자 기준으로 사용한다. 한 사람이 승인한 추정치도 실측으로 전환하지 않는다. 같은 값이 바뀌면 case.json 한 곳을 고치고 다시 실행한 뒤 모든 버전의 관련 표를 대조한다. input_errors(종료코드 2)는 자료 오류, diagnostics(정상 종료한 검산 결과)는 기획·증빙상 미충족이다. 현장 제출 준비 여부는 모든 원자료와 별도로 판정한다.

선택 사항인 --html은 지표→산식·범위·목표출처→원장행으로 연결되는 로컬 탐색 문서를 저장한다. 식별자나 수치가 담기므로 결과물 공유 범위는 원장 권한을 따른다. 생산수가 0인 로트는 에너지 합계에 포함하되 LOT 단일 클래스 검사에서 제외한다.
