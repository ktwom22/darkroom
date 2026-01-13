#!/usr/bin/env python3
"""
Debug script to check authentication issues
"""
import os
import sys
from main import app, db, User
from werkzeug.security import check_password_hash

def check_users():
    with app.app_context():
        users = User.query.all()
        print(f"\n=== Found {len(users)} users in database ===")
        for user in users:
            print(f"\nUser ID: {user.id}")
            print(f"Email: {user.email}")
            print(f"Password hash (first 50 chars): {user.password[:50] if user.password else 'None'}...")
            print(f"Business: {user.business_name}")
            
        return users

def test_login(email, password):
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        if not user:
            print(f"\n❌ No user found with email: {email}")
            return False
        
        print(f"\n✓ User found: {user.email}")
        password_match = check_password_hash(user.password, password)
        print(f"Password matches: {password_match}")
        
        if not password_match:
            print(f"\n⚠️  Debug Info:")
            print(f"   - Hash starts with: {user.password[:20]}...")
            print(f"   - Password length: {len(password)}")
            print(f"   - Password value: {'*' * len(password)}")
        
        return password_match

if __name__ == "__main__":
    if len(sys.argv) > 2:
        email = sys.argv[1]
        password = sys.argv[2]
        print(f"\nTesting login for: {email}")
        test_login(email, password)
    else:
        check_users()
        print("\n\nUsage: python debug_auth.py <email> <password>")
        print("   or: python debug_auth.py  (to list all users)")
