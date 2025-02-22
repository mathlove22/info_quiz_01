import streamlit as st
import os
import json
import random
import re
import datetime
import time  # 입력 속도 측정을 위해 time 모듈 사용
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --------------------------------------------------------------
# 커스텀 CSS (붙여넣기 차단 JavaScript는 제거)
# --------------------------------------------------------------
st.markdown(
    """
    <style>
    .main {
        background-color: #f0f2f6;
    }
    .title {
        font-family: 'Helvetica', sans-serif;
        font-size: 2.8em;
        color: #3366cc;
        text-align: center;
        margin-bottom: 20px;
    }
    .header {
        font-family: 'Helvetica', sans-serif;
        font-size: 1.75em;
        color: #333;
        margin-top: 20px;
        margin-bottom: 10px;
    }
    .subheader {
        font-family: 'Helvetica', sans-serif;
        font-size: 1.25em;
        color: #555;
    }
    .score-card {
        background-color: #ffffff;
        border-radius: 10px;
        padding: 20px;
        margin-top: 20px;
        box-shadow: 0 4px 8px rgba(0,0,0,0.1);
    }
    .criterion-item {
        margin-bottom: 10px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# --------------------------------------------------------------
# 구글 시트 인증 및 데이터 로드
# --------------------------------------------------------------
scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

credentials = ServiceAccountCredentials.from_json_keyfile_dict(
    st.secrets["gcp_service_account"], scope
)
gc = gspread.authorize(credentials)

# [general] 섹션에서 스프레드시트 ID 읽기
general_secrets = st.secrets.get("general", {})
questions_sheet_id = general_secrets.get("questions_sheet_id")
criteria_sheet_id = general_secrets.get("criteria_sheet_id")
results_sheet_id  = general_secrets.get("results_sheet_id")  # 기록용 시트 ID (쓰기 권한 필요)

if not questions_sheet_id or not criteria_sheet_id:
    st.error("문제 또는 채점 기준 스프레드시트 ID가 설정되지 않았습니다.")
    st.stop()

# 문제/채점 기준 데이터를 세션에 로드
if "questions_data" not in st.session_state:
    questions_sh = gc.open_by_key(questions_sheet_id)
    questions_ws = questions_sh.get_worksheet(0)
    st.session_state.questions_data = questions_ws.get_all_records()

if "criteria_data" not in st.session_state:
    criteria_sh = gc.open_by_key(criteria_sheet_id)
    criteria_ws = criteria_sh.get_worksheet(0)
    criteria = criteria_ws.get_all_records()
    criteria.sort(key=lambda x: float(x["최소비율"]), reverse=True)
    st.session_state.criteria_data = criteria

questions_data = st.session_state.questions_data
criteria_data = st.session_state.criteria_data

# --------------------------------------------------------------
# 문제 목록에서 랜덤 6문항 선택
# --------------------------------------------------------------
if len(questions_data) < 6:
    st.error("문제 데이터가 6문항 미만입니다.")
    st.stop()

if "selected_questions" not in st.session_state:
    st.session_state.selected_questions = random.sample(questions_data, 6)
selected_questions = st.session_state.selected_questions

# --------------------------------------------------------------
# 제출 상태 초기화
# --------------------------------------------------------------
if "submitted" not in st.session_state:
    st.session_state.submitted = False

# --------------------------------------------------------------
# 입력 속도 측정 관련 세션 값
# --------------------------------------------------------------
if "last_time" not in st.session_state:
    st.session_state.last_time = time.time()
if "last_length" not in st.session_state:
    st.session_state.last_length = 0

# 최고 속도를 기록하기 위한 변수 (기존에 없으면 0.0으로 초기화)
if "peak_speed" not in st.session_state:
    st.session_state.peak_speed = 0.0

# --------------------------------------------------------------
# 앱 UI: 제목, 학생 정보
# --------------------------------------------------------------
st.markdown('<div class="title">논술형 문제 채점 시스템</div>', unsafe_allow_html=True)

with st.sidebar:
    st.markdown('<div class="header">학생 정보</div>', unsafe_allow_html=True)
    submitted_flag = st.session_state.submitted
    student_id = st.text_input("학번", key="student_id", disabled=submitted_flag)
    student_name = st.text_input("이름", key="student_name", disabled=submitted_flag)

submit_disabled = st.session_state.submitted or not (student_id.strip() and student_name.strip())

# --------------------------------------------------------------
# 문제 및 답안 (제출 후 수정 불가)
# --------------------------------------------------------------
st.markdown('<div class="header">문제 및 답안</div>', unsafe_allow_html=True)

answers = {}
for idx, q in enumerate(selected_questions):
    st.markdown(f'<div class="subheader">문제 {idx+1}</div>', unsafe_allow_html=True)
    st.info(q["문제"], icon="✍️")
    ans = st.text_area(
        f"문제 {idx+1} 답안 작성:",
        key=f"answer_{idx}",
        disabled=submitted_flag
    )
    answers[idx] = ans

# --------------------------------------------------------------
# 입력 속도 계산 (초당 입력 문자 수)
# --------------------------------------------------------------
current_time = time.time()
total_answer_length = sum(len(a) for a in answers.values())
time_diff = current_time - st.session_state.last_time
char_diff = total_answer_length - st.session_state.last_length

typing_speed = char_diff / time_diff if time_diff else 0  # chars/sec

# 최고 속도 업데이트
if typing_speed > st.session_state.peak_speed:
    st.session_state.peak_speed = typing_speed

# 현재 속도 표시
st.markdown(f"**현재 추정 입력 속도**: {typing_speed:.2f} chars/sec")

# 맨 아래 경고는 "최고 속도" 기준으로 표시하기 위해 실제 경고는 제출 전에 출력
# 세션 상태 갱신
st.session_state.last_time = current_time
st.session_state.last_length = total_answer_length

# --------------------------------------------------------------
# 함수: Gemini 2.0 모델로 답안 채점
# --------------------------------------------------------------
def grade_all_answers_with_gemini(combined_prompt):
    api_key = st.secrets["gcp_service_account"].get("GEMINI_API_KEY")
    if not api_key:
        st.error("GEMINI_API_KEY 환경 변수가 설정되어 있지 않습니다.")
        return None
    genai.configure(api_key=api_key)
    
    generation_config = {
        "temperature": 1,
        "top_p": 0.95,
        "top_k": 40,
        "max_output_tokens": 8192,
        "response_mime_type": "text/plain",
    }
    model = genai.GenerativeModel(
        model_name="gemini-2.0-flash-exp",
        generation_config=generation_config,
    )
    chat_session = model.start_chat(history=[])
    response = chat_session.send_message(combined_prompt)
    
    try:
        json_match = re.search(r'({.*})', response.text, re.DOTALL)
        if json_match:
            pure_json = json_match.group(1)
            result = json.loads(pure_json)
        else:
            st.error("적절한 JSON 형태의 응답을 찾지 못했습니다.")
            result = None
    except Exception as e:
        st.error("응답 파싱 실패: " + str(e))
        result = None

    return result

# --------------------------------------------------------------
# 제출 버튼
#   - 맨 밑 경고: peak_speed가 25 초과하면 붙여넣기 의심
# --------------------------------------------------------------
if st.button("제출", disabled=submit_disabled):
    if not (student_id.strip() and student_name.strip()):
        st.error("학번과 이름을 반드시 입력해 주세요.")
    elif any(not ans.strip() for ans in answers.values()):
        st.error("모든 문제에 대해 답안을 작성해 주세요.")
    else:
        # 제출 시점에 최고 속도가 25 이상인지 확인
        if st.session_state.peak_speed > 25:
            st.warning("해당 시험에서 매우 빠른 입력(붙여넣기 의심)이 감지되었습니다. (최고속도 기준)")

        # 채점 프롬프트 생성
        combined_prompt = "아래는 각 문제와 모범답안, 학생의 답안입니다:\n\n"
        for idx, q in enumerate(selected_questions):
            combined_prompt += f"문제 {idx+1}:\n"
            combined_prompt += "문제:\n" + q["문제"] + "\n"
            combined_prompt += "모범답안:\n" + q["모범답안"] + "\n"
            combined_prompt += "학생의 답안:\n" + answers[idx] + "\n"
            combined_prompt += "---------------------\n"
        
        # 채점 기준
        criteria_text = "채점 기준은 다음과 같습니다:\n"
        for crit in criteria_data:
            criteria_text += f"최소비율 {crit['최소비율']}%: {crit['설명']} (점수: {crit['점수']})\n"
        combined_prompt += "\n" + criteria_text
        
        # 기대 JSON 예시
        expected_json = (
            "\n위 정보를 바탕으로, 각 문제 별 학생 답안과 모범답안의 유사도를 0부터 100 사이의 백분율로 산출하고, "
            "해당 기준에 따라 점수를 부여하십시오. 최종 결과는 아래 JSON 형식으로 출력해 주세요:\n"
            '{ "문제1": {"score": 0, "유사도": 0.0, "설명": ""}, '
            '"문제2": {"score": 0, "유사도": 0.0, "설명": ""}, '
            '"문제3": {"score": 0, "유사도": 0.0, "설명": ""}, '
            '"문제4": {"score": 0, "유사도": 0.0, "설명": ""}, '
            '"문제5": {"score": 0, "유사도": 0.0, "설명": ""}, '
            '"문제6": {"score": 0, "유사도": 0.0, "설명": ""}, '
            '"총점": 0 }'
        )
        combined_prompt += expected_json

        with st.spinner("채점 중입니다... 잠시만 기다려 주세요!"):
            result = grade_all_answers_with_gemini(combined_prompt)
        
        if result:
            try:
                # 채점 결과 표시
                score_blocks = ""
                total_score = result.get("총점", 0)
                for i in range(6):
                    q_result = result.get(f"문제{i+1}", {})
                    score = q_result.get("score", 0)
                    similarity = q_result.get("유사도", 0.0)
                    explanation = q_result.get("설명", "")
                    score_blocks += (
                        f'<p class="criterion-item"><strong>문제 {i+1}</strong>: '
                        f'{score}점, 유사도: {similarity:.2f}%, {explanation}</p>'
                    )
                
                result_card = f"""
                <div class="score-card">
                    <h2>{student_name} ({student_id})님의 채점 결과</h2>
                    <h3>총점: {total_score}점</h3>
                    <hr>
                    {score_blocks}
                </div>
                """
                st.markdown(result_card, unsafe_allow_html=True)
                
                # 최고 속도가 25 이상이면 의심
                suspicious_flag = "Y" if st.session_state.peak_speed > 25 else "N"

                # 결과 기록 (구글 시트에 append)
                if results_sheet_id:
                    submission_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    row = [student_id, student_name, submission_time, total_score]
                    
                    # 문제별 세부 기록
                    for i in range(6):
                        q = selected_questions[i]
                        q_result = result.get(f"문제{i+1}", {})
                        score = q_result.get("score", 0)
                        remark = q_result.get("설명", "")
                        row.extend([q["문제"], answers.get(i, ""), score, remark])
                    
                    # (peak_speed, suspicious_flag) 추가
                    row.extend([f"{st.session_state.peak_speed:.2f}", suspicious_flag])

                    results_sh = gc.open_by_key(results_sheet_id)
                    results_ws = results_sh.get_worksheet(0)
                    results_ws.append_row(row)
                else:
                    st.warning("결과 기록용 스프레드시트 ID(results_sheet_id)가 설정되어 있지 않습니다.")
                
                # 제출 완료 후 재제출 / 수정 불가
                st.session_state.submitted = True

            except Exception as e:
                st.error("채점 결과 처리 중 오류 발생: " + str(e))
