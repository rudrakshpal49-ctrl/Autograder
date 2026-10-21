
from services.submission_service import (
    save_submission,
    get_student_submissions,
    MAX_SOURCE_SIZE,
)

import streamlit as st

from services.authentication import (
    authenticate_user,
    get_user_by_id,
)

from services.assignment_service import (
    create_assignment,
    get_all_assignments,
)


# ---------------------------------
# APPLICATION CONFIGURATION
# ---------------------------------

st.set_page_config(
    page_title="AutoGrader",
    page_icon="🎓",
    layout="wide",
)


# ---------------------------------
# SESSION INITIALIZATION
# ---------------------------------

if "user_id" not in st.session_state:
    st.session_state.user_id = None


# ---------------------------------
# LOGIN PAGE
# ---------------------------------

def show_login():
    st.title("🎓 AutoGrader")

    st.subheader("Student and Teacher Login")

    with st.form("login_form"):
        username = st.text_input("Username")

        password = st.text_input(
            "Password",
            type="password",
        )

        submitted = st.form_submit_button("Login")

    if submitted:
        user = authenticate_user(username, password)

        if user is None:
            st.error("Invalid username or password.")
        else:
            st.session_state.user_id = user["id"]
            st.rerun()


# ---------------------------------
# STUDENT DASHBOARD
# ---------------------------------


def student_dashboard(user):
    st.title("👨‍🎓 Student Dashboard")
    st.write(f"Welcome, {user['username']}!")

    st.subheader("Available Assignments")

    assignments = get_all_assignments()

    if not assignments:
        st.info(
            "No assignments are currently available. "
            "Please ask your teacher to create one."
        )
        return

    # Prepare assignment choices.
    assignment_lookup = {
        assignment["id"]: assignment
        for assignment in assignments
    }

    selected_assignment_id = st.selectbox(
        "Select an assignment",
        options=list(assignment_lookup.keys()),
        format_func=lambda assignment_id: (
            f"Assignment {assignment_id}: "
            f"{assignment_lookup[assignment_id]['title']}"
        ),
    )

    selected_assignment = assignment_lookup[
        selected_assignment_id
    ]

    st.markdown("### Problem Statement")
    st.write(selected_assignment["description"])

    st.divider()

    # ---------------------------------
    # C PROGRAM UPLOAD
    # ---------------------------------

    st.subheader("Submit Your C Program")

    with st.form("submission_form"):
        uploaded_file = st.file_uploader(
            "Choose your C source file",
            type=["c"],
            help="Only UTF-8 .c files are supported (maximum 100 KiB).",
        )

        submitted = st.form_submit_button(
            "Submit Program"
        )

    if submitted:
        if uploaded_file is None:
            st.warning(
                "Please select a .c file before submitting."
            )

        else:
            source_bytes = uploaded_file.getvalue()

            if len(source_bytes) > MAX_SOURCE_SIZE:
                st.error(
                    "File too large. The maximum allowed size is 100 KiB."
                )

            else:
                try:
                    result = save_submission(
                        student_id=user["id"],
                        assignment_id=selected_assignment_id,
                        filename=uploaded_file.name,
                        source_bytes=source_bytes,
                    )

                except (ValueError, PermissionError) as error:
                    st.error(str(error))

                else:
                    st.success(
                        "Your C program was submitted successfully!"
                    )

                    st.write(
                        "**Submission ID:**",
                        result["submission_id"],
                    )

                    st.write(
                        "**Assignment ID:**",
                        result["assignment_id"],
                    )

                    st.code(
                        result["source_hash"],
                        language="text",
                    )

                    st.caption(
                        "The SHA-256 value identifies the uploaded "
                        "source file. Grading has not started yet."
                    )

    st.divider()

    # ---------------------------------
    # SUBMISSION HISTORY
    # ---------------------------------

    st.subheader("My Submission History")

    submissions = get_student_submissions(
        student_id=user["id"]
    )

    if not submissions:
        st.info("You have not submitted any programs yet.")

    else:
        for submission in submissions:
            with st.expander(
                f"Submission #{submission['id']} "
                f"— Assignment #{submission['assignment_id']}"
            ):
                st.write(
                    "**Submitted at:**",
                    str(submission["created_at"]),
                )

                st.write(
                    "**Assignment ID:**",
                    submission["assignment_id"],
                )

                st.write("**Source SHA-256:**")

                st.code(
                    submission["source_hash"],
                    language="text",
                )

                st.caption(
                    "Status: Source file saved. "
                    "Automatic grading will be added next."
                )



# ---------------------------------
# TEACHER DASHBOARD
# ---------------------------------

def teacher_dashboard(user):
    st.title("👩‍🏫 Teacher Dashboard")

    st.write(f"Welcome, {user['username']}!")

    tab1, tab2 = st.tabs([
        "Create Assignment",
        "View Assignments",
    ])

    with tab1:
        st.subheader("Create a New Assignment")

        with st.form("create_assignment_form"):
            title = st.text_input(
                "Assignment Title"
            )

            description = st.text_area(
                "Problem Statement",
                height=180,
            )

            submitted = st.form_submit_button(
                "Create Assignment"
            )

        if submitted:
            try:
                assignment_id = create_assignment(
                    teacher_id=user["id"],
                    title=title,
                    description=description,
                )

                st.success(
                    f"Assignment #{assignment_id} "
                    "created successfully!"
                )

            except ValueError as error:
                st.error(str(error))

            except PermissionError:
                st.error(
                    "You are not authorized to create assignments."
                )

    with tab2:
        st.subheader("Saved Assignments")

        assignments = get_all_assignments()

        if not assignments:
            st.info("No assignments have been saved yet.")

        else:
            for assignment in assignments:
                with st.expander(
                    f"Assignment {assignment['id']}: "
                    f"{assignment['title']}"
                ):
                    st.write(assignment["description"])


# ---------------------------------
# MAIN APPLICATION
# ---------------------------------

def main():
    if st.session_state.user_id is None:
        show_login()
        return

    user = get_user_by_id(st.session_state.user_id)

    if user is None:
        st.session_state.user_id = None
        st.rerun()

    st.sidebar.title("🎓 AutoGrader")

    st.sidebar.write(
        f"Logged in as: {user['username']}"
    )

    st.sidebar.write(
        f"Role: {user['role'].title()}"
    )

    if st.sidebar.button("Logout"):
        st.session_state.user_id = None
        st.rerun()

    if user["role"] == "student":
        student_dashboard(user)

    elif user["role"] == "teacher":
        teacher_dashboard(user)

    else:
        st.error("Invalid account role.")


if __name__ == "__main__":
    main()
