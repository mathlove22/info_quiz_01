import streamlit as st
import os
import random
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from difflib import SequenceMatcher

# --------------------------------------------------------------
# 구글 시트 인증 및 데이터 로드
# --------------------------------------------------------------
# Google Sheets API 접근 범위
scope = [
    'https://www.googleapis.com/auth/spreadsheets.readonly',
    'https://www.googleapis.com/auth/drive.readonly'
]

# st.secrets에 서비스 계정 JSON 정보가 저장되어 있다고 가정합니다.
# 예시: st.secrets["gcp_service_account"]에 JSON 키 값이 dict형태로 저장되어 있음.
credentials = ServiceAccountCredentials.from_json_keyfile_dict(
    st.secrets["gcp_service_account"], scope
)
gc = gspread.authorize(credentials)

# st.secrets에 지정된 구글 시트 ID (문제와 채점 기준 전용)
questions_sheet_id = st.secrets["questions_sheet_id"]  # 문제 및 모범답안 데이터가 있는 시트 ID
criteria_sheet_id = st.secrets["criteria_sheet_id"]      # 채점 기준 데이터가 있는 시트 ID

# 문제 데이터 로드 (예: 첫 번째 워크시트의 각 행은 {"문제": ..., "모범답안": ...} 형식)
questions_sh = gc.open_by_key(questions_sheet_id)
questions_ws = questions_sh.get_worksheet(0)
questions_data = questions_ws.get_all_records()  # 리스트의 각 원소는 dict

# 채점 기준 데이터 로드 (예: 각 행은 {"최소비율": 80, "점수": 5, "설명": "80% 이상이면 5점"} 형식)
criteria_sh = gc.open_by_key(criteria_sheet_id)
criteria_ws = criteria_sh.get_worksheet(0)
criteria_data = criteria_ws.get_all_records()  

# 채점 기준을 내림차순으로 정렬(높은 최소비율부터 체크)
criteria_data.sort(key=lambda x: float(x["최소비율"]), reverse=True)

# --------------------------------------------------------------
# 문제은행에서 랜덤으로 문제 선택
# --------------------------------------------------------------
if not questions_data:
    st.error("문제 데이터가 없습니다.")
    st.stop()
selected_question = random.choice(questions_data)

# --------------------------------------------------------------
# Streamlit 커스텀 CSS 적용
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
# 앱 구성: 제목, 사이드바, 문제 및 답안 입력
# --------------------------------------------------------------
# 앱 제목
st.markdown('<div class="title">논술형 문제 채점 시스템</div>', unsafe_allow_html=True)

# 사이드바: 학생 정보 입력
with st.sidebar:
    st.markdown('<div class="header">학생 정보</div>', unsafe_allow_html=True)
    student_id = st.text_input("학번", key="student_id")
    student_name = st.text_input("이름", key="student_name")

# 본문: 문제 및 답안 제시
st.markdown('<div class="header">문제</div>', unsafe_allow_html=True)
# 선택된 문제 출력 (아이콘 추가)
st.info(selected_question["문제"], icon="✍️")

st.markdown('<div class="header">답안 작성</div>', unsafe_allow_html=True)
answer = st.text_area("답안을 아래에 작성해주세요:", height=250)

# --------------------------------------------------------------
# 유사도 계산 및 채점 함수
# --------------------------------------------------------------
def compute_similarity(text1, text2):
    """
    두 텍스트의 유사도를 0~1 사이의 값으로 반환합니다.
    """
    return SequenceMatcher(None, text1, text2).ratio()

def grade_answer(student_answer, model_answer):
    """
    학생 답안과 모범답안 간의 유사도를 기준으로 점수와 설명을 반환합니다.
    기준: 기준 시트에 정의된 '최소비율' 내림차순 순서대로 비교.
    """
    similarity = compute_similarity(student_answer, model_answer)
    similarity_percent = similarity * 100
    score = 1  # 기본 점수
    explanation = ""
    for criteria in criteria_data:
        try:
            threshold = float(criteria["최소비율"])
            if similarity_percent >= threshold:
                score = int(criteria["점수"])
                explanation = criteria.get("설명", "")
                break
        except Exception:
            continue
    return score, similarity_percent, explanation

# --------------------------------------------------------------
# 제출 버튼 및 결과 처리
# --------------------------------------------------------------
if st.button("제출"):
    if not answer.strip():
        st.error("답안을 작성해 주세요.")
    else:
        with st.spinner("채점 중입니다... 잠시 기다려 주세요!"):
            score, similarity_percent, criteria_desc = grade_answer(
                answer, selected_question["모범답안"]
            )
        result_card = f"""
        <div class="score-card">
            <h2>{student_name} ({student_id})님의 채점 결과</h2>
            <h3>총점: {score}점</h3>
            <p>모범답안과의 유사도: {similarity_percent:.2f}%</p>
            <hr>
            <p class="criterion-item"><strong>채점 기준</strong>: {criteria_desc}</p>
        </div>
        """
        st.markdown(result_card, unsafe_allow_html=True)
