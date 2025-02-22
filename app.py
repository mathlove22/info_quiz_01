import streamlit as st
import os
import json
import random
import re
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --------------------------------------------------------------
# 커스텀 CSS 적용
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
# Google Sheets API 접근 범위 설정
scope = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly"
]

# st.secrets에 저장된 서비스 계정 정보를 사용
credentials = ServiceAccountCredentials.from_json_keyfile_dict(
    st.secrets["gcp_service_account"], scope
)
gc = gspread.authorize(credentials)

# st.secrets에 설정한 구글 시트 ID들 (문제와 채점 기준)
questions_sheet_id = st.secrets["general"]["questions_sheet_id"]
criteria_sheet_id = st.secrets["general"]["criteria_sheet_id"]

# 문제 시트: 각 행은 {"문제": ..., "모범답안": ...} 형식이어야 함.
questions_sh = gc.open_by_key(questions_sheet_id)
questions_ws = questions_sh.get_worksheet(0)
questions_data = questions_ws.get_all_records()

# 채점 기준 시트: 각 행은 {"최소비율": 80, "점수": 5, "설명": "80% 이상이면 5점 채점"} 형식이어야 함.
criteria_sh = gc.open_by_key(criteria_sheet_id)
criteria_ws = criteria_sh.get_worksheet(0)
criteria_data = criteria_ws.get_all_records()

# 채점 기준을 내림차순(높은 최소비율부터)으로 정렬 (LLM 프롬프트에 활용)
criteria_data.sort(key=lambda x: float(x["최소비율"]), reverse=True)

# --------------------------------------------------------------
# 문제은행에서 랜덤으로 문제 선택
# --------------------------------------------------------------
if not questions_data:
    st.error("문제 데이터가 없습니다.")
    st.stop()
selected_question = random.choice(questions_data)

# --------------------------------------------------------------
# 앱 UI 구성: 제목, 학생 정보 입력, 문제 및 답안 입력
# --------------------------------------------------------------
st.markdown('<div class="title">논술형 문제 채점 시스템</div>', unsafe_allow_html=True)

with st.sidebar:
    st.markdown('<div class="header">학생 정보</div>', unsafe_allow_html=True)
    student_id = st.text_input("학번", key="student_id")
    student_name = st.text_input("이름", key="student_name")

st.markdown('<div class="header">문제</div>', unsafe_allow_html=True)
st.info(selected_question["문제"], icon="✍️")

st.markdown('<div class="header">답안 작성</div>', unsafe_allow_html=True)
answer = st.text_area("답안을 아래에 작성해주세요:", height=250)

# --------------------------------------------------------------
# Gemini 2.0 LLM을 이용한 채점 함수
# --------------------------------------------------------------
def grade_answer_with_gemini(answer, question, model_answer, criteria_data):
    # Gemini API 인증
    api_key = os.getenv("GEMINI_API_KEY")
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

    # Gemini 2.0 Flash Experimental 모델 생성 및 채팅 세션 시작
    model = genai.GenerativeModel(
        model_name="gemini-2.0-flash-exp",
        generation_config=generation_config,
    )
    chat_session = model.start_chat(history=[])

    # 채점 기준을 쉽게 읽을 수 있도록 문자열로 구성. 각 행은 "최소비율 {최소비율}%: {설명} (점수: {점수})" 형식.
    criteria_text = ""
    for crit in criteria_data:
        criteria_text += f"최소비율 {crit['최소비율']}%: {crit['설명']} (점수: {crit['점수']})\n"

    # 최종 프롬프트: 문제, 모범답안, 학생 답안을 모두 제공한 후, LLM에게
    # 유사도(0~100%)와 채점 점수, 설명을 JSON 형태로 반환하도록 요청.
    prompt = f"""
문제:
{question}

모범답안:
{model_answer}

학생의 답안:
{answer}

위 정보를 참고하여 학생의 답안이 모범답안과 얼마나 유사한지 평가해 주세요.
채점 기준은 아래와 같습니다:
{criteria_text}

학생 답안과 모범답안의 유사도를 0부터 100 사이의 백분율로 산출한 후, 그 유사도에 맞는 점수를 부여하십시오.
최종 결과는 아래와 같은 JSON 형식으로 출력해 주세요:
{{"score": 0, "유사도": 0.0, "설명": ""}}
    """
    
    # Gemini LLM에 프롬프트 전송
    response = chat_session.send_message(prompt)

    # Gemini 응답 메시지에서 순수 JSON 부분 추출 (정규표현식 사용)
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
# 제출 버튼 및 Gemini를 통한 채점 처리
# --------------------------------------------------------------
if st.button("제출"):
    if not answer.strip():
        st.error("답안을 입력해 주세요.")
    else:
        with st.spinner("채점 중입니다... 잠시 기다려 주세요!"):
            result = grade_answer_with_gemini(
                answer,
                selected_question["문제"],
                selected_question["모범답안"],
                criteria_data
            )

        if result:
            try:
                total_score = result.get("score", 0)
                similarity = result.get("유사도", 0.0)
                explanation = result.get("설명", "")
                result_card = f"""
                <div class="score-card">
                    <h2>{student_name} ({student_id})님의 채점 결과</h2>
                    <h3>총점: {total_score}점</h3>
                    <p>모범답안과의 유사도: {similarity:.2f}%</p>
                    <hr>
                    <p class="criterion-item"><strong>채점 기준</strong>: {explanation}</p>
                </div>
                """
                st.markdown(result_card, unsafe_allow_html=True)
            except Exception as e:
                st.error("채점 결과 처리 중 오류 발생: " + str(e))
