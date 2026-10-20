
import argparse
from getpass import getpass

from services.authentication import create_user


def main():
    parser = argparse.ArgumentParser(
        description="Create an AutoGrader user account."
    )

    parser.add_argument(
        "--role",
        choices=["student", "teacher"],
        required=True,
    )

    parser.add_argument(
        "--username",
        required=True,
    )

    args = parser.parse_args()

    password = getpass("Enter password: ")
    confirmation = getpass("Confirm password: ")

    if password != confirmation:
        print("Passwords do not match.")
        return

    try:
        user_id = create_user(
            username=args.username,
            password=password,
            role=args.role,
        )

        print(f"Account created! User ID: {user_id}")

    except ValueError as error:
        print(f"Account creation failed: {error}")


if __name__ == "__main__":
    main()
