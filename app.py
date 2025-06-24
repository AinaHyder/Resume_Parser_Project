from flask import Flask, request, jsonify, render_template, send_from_directory, make_response
import os
import json
import csv
import io
from werkzeug.utils import secure_filename
import uuid
from datetime import datetime
import re
import pymongo
from pymongo import MongoClient
import PyPDF2
import pdfplumber
import google.generativeai as genai
import requests

from dotenv import load_dotenv

# Try to import docx, but don't fail if it's not available
try:
    import docx
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False
    print("python-docx not installed. DOCX parsing will be limited.")

app = Flask(__name__)

# Configure upload folder and allowed extensions
UPLOAD_FOLDER = 'uploads'
CV_FOLDER = 'cv_files'  # New folder for storing CV files with URLs
ALLOWED_EXTENSIONS = {'pdf', 'docx', 'doc', 'txt', 'rtf', 'csv'}

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

if not os.path.exists(CV_FOLDER):
    os.makedirs(CV_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['CV_FOLDER'] = CV_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload size

# Google Gemini AI Setup
load_dotenv()  # Load variables from .env

try:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("API key not found. Set GOOGLE_API_KEY in .env")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("models/gemini-1.5-pro")
    gemini_available = True
    print("Google Gemini AI setup successful")
except Exception as e:
    print(f"Google Gemini AI setup error: {e}")
    gemini_available = False


# MongoDB connection
try:
    client = MongoClient('mongodb://localhost:27017/')
    db = client['resume_parser']
    resumes_collection = db['resumes']
    applications_collection = db['applications']
    print("MongoDB connection successful")
    mongodb_available = True
except Exception as e:
    print(f"MongoDB connection error: {e}")
    # Fallback to in-memory storage
    mongodb_available = False

# Initialize in-memory storage
resumes_data = []
applications_data = []

# Helper function to check allowed file extensions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Helper function to generate CV URL
def generate_cv_url(original_filename):
    """Generate a unique URL for the CV file"""
    unique_id = str(uuid.uuid4())
    file_extension = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else 'pdf'
    cv_filename = f"{unique_id}.{file_extension}"
    cv_url = f"/view_cv/{unique_id}"
    return cv_url, cv_filename

# Helper function to extract text from PDF using pdfplumber
def extract_text_from_pdf(file_path):
    try:
        text = ""
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text += page.extract_text() or ""
        return text
    except Exception as e:
        print(f"Error extracting text with pdfplumber: {e}")
        try:
            text = ""
            with open(file_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                for page_num in range(len(pdf_reader.pages)):
                    text += pdf_reader.pages[page_num].extract_text() + "\n"
            return text
        except Exception as e2:
            print(f"Error extracting text with PyPDF2: {e2}")
            return ""

# Helper function to extract text from DOCX
def extract_text_from_docx(file_path):
    text = ""
    if not DOCX_AVAILABLE:
        print("python-docx not installed. Cannot extract text from DOCX.")
        return "ERROR: python-docx not installed. Cannot extract text from DOCX."
    
    try:
        doc = docx.Document(file_path)
        for para in doc.paragraphs:
            text += para.text + "\n"
    except Exception as e:
        print(f"Error extracting text from DOCX: {e}")
    return text

# Helper function to extract text from CSV
def extract_text_from_csv(file_path):
    text = ""
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            import csv
            csv_reader = csv.reader(file)
            for row in csv_reader:
                text += " ".join(row) + "\n"
    except Exception as e:
        print(f"Error extracting text from CSV: {e}")
    return text

# Helper function to extract text from TXT
def extract_text_from_txt(file_path):
    text = ""
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            text = file.read()
    except UnicodeDecodeError:
        try:
            with open(file_path, 'r', encoding='latin-1') as file:
                text = file.read()
        except Exception as e:
            print(f"Error extracting text from TXT with latin-1 encoding: {e}")
    except Exception as e:
        print(f"Error extracting text from TXT: {e}")
    return text

# Extract text based on file type
def extract_text(file_path):
    file_extension = file_path.rsplit('.', 1)[1].lower()
    if file_extension == 'pdf':
        return extract_text_from_pdf(file_path)
    elif file_extension in ['docx', 'doc']:
        return extract_text_from_docx(file_path)
    elif file_extension == 'csv':
        return extract_text_from_csv(file_path)
    elif file_extension in ['txt', 'rtf']:
        return extract_text_from_txt(file_path)
    return ""

# Function to Extract Resume Details using Gemini AI
def parse_resume_with_ai(resume_text):
    prompt = f"""
    You are a resume parsing assistant. Extract details from the following resume text and return them in structured JSON format.
    
    ### Resume Text:
    {resume_text}
    
    ### Return JSON Format:
    {{
      "Full Name": "John Doe",
      "Contact Number": "123-456-7890",
      "Email Address": "johndoe@example.com",
      "Location": "New York, USA",
      "LinkedIn": "https://www.linkedin.com/in/johndoe",
      "GitHub": "https://github.com/johndoe",
      "Skills": {{ "Technical": ["Python", "Java"], "Soft": ["Communication"] }},
      "Education": [{{ "Degree": "B.Sc. Computer Science", "Institution": "XYZ University", "Years": "2015-2019" }}],
      "Work Experience": [{{ "Company": "ABC Corp", "Role": "Software Engineer", "Years": "3" }}],
      "Certifications": ["AWS Certified Developer"],
      "Languages": ["English", "Spanish"],
      "Suggested Category": "Software Development",
      "Recommended Roles": ["Backend Developer", "Full Stack Developer"]
    }}
    
    IMPORTANT: For the Full Name field, make sure to extract ONLY the person's name without any prefixes like "Contact" or "Name:". For LinkedIn URL, extract the COMPLETE URL including https://www.linkedin.com/in/ part.
    """
    
    try:
        response = model.generate_content(prompt).text
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        if json_match:
            response_clean = json_match.group(0)
            return json.loads(response_clean)
        else:
            return {"error": "No valid JSON detected in AI response"}
    except json.JSONDecodeError:
        return {"error": "Invalid JSON response from AI"}
    except Exception as e:
        return {"error": str(e)}

# Section identification patterns
SECTION_PATTERNS = {
    'personal_info': [r'personal\s+information', r'contact\s+information', r'contact', r'personal', r'about\s+me'],
    'education': [r'education', r'academic', r'qualification', r'degree'],
    'experience': [r'experience', r'employment', r'work\s+history', r'professional\s+experience', r'work\s+experience'],
    'skills': [r'skills', r'technical\s+skills', r'competencies', r'expertise'],
    'projects': [r'projects', r'personal\s+projects', r'academic\s+projects'],
    'certifications': [r'certifications', r'certificates', r'accreditations'],
    'languages': [r'languages', r'language\s+proficiency'],
    'interests': [r'interests', r'hobbies', r'activities'],
    'references': [r'references', r'referees']
}

# Improved regex patterns for better extraction
NAME_PATTERNS = [
    r'^\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s*$',
    r'name[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})',
    r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s*\n',
    r'curriculum\s+vitae\s*(?:of|for)?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})',
    r'resume\s*(?:of|for)?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})',
    r'([A-Z][A-Z\s]+)',
    r'([A-Z][a-z]+\s+[A-Z][a-z]+)',
    r'^([A-Z][a-z]*\.?\s+[A-Z][a-z]+)',
    r'^\s*([A-Za-z\.\s]{2,30})\s*$'
]

EMAIL_PATTERNS = [
    r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
    r'email[:\s]+([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,})',
    r'e-mail[:\s]+([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,})',
    r'mail[:\s]+([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,})'
]

PHONE_PATTERNS = [
    r'(?:phone|mobile|cell|contact|tel)[:\s]+(\+?[\d\s\-\.]{7,})',
    r'\b(\+\d{1,3}[\s\-\.]?\d{3}[\s\-\.]?\d{3}[\s\-\.]?\d{4})\b',
    r'\b(\d{3}[\s\-\.]?\d{3}[\s\-\.]?\d{4})\b',
    r'\b(\d{4}[\s\-\.]?\d{3}[\s\-\.]?\d{3})\b',
    r'\b(\d{3,4}[\s\-\.]?\d{6,7})\b'
]

LOCATION_PATTERNS = [
    r'(?:location|address|city|residence)[:\s]+([A-Za-z0-9\s,\.\-]+)',
    r'(?:state|province|country)[:\s]+([A-Za-z\s,\.]+)',
    r'(?:zip|postal\s+code)[:\s]+([A-Za-z0-9\s\-]+)'
]

# Enhanced patterns for LinkedIn and GitHub URLs - FIXED
LINKEDIN_PATTERNS = [
    r'(https?://(?:www\.)?linkedin\.com/in/[a-zA-Z0-9_-]+/?)',  # Complete URL
    r'(?:linkedin|linked\s*in)[:\s]+(https?://(?:www\.)?linkedin\.com/in/[a-zA-Z0-9_-]+/?)',  # Labeled complete URL
    r'(?:linkedin|linked\s*in)[:\s]+((?:www\.)?linkedin\.com/in/[a-zA-Z0-9_-]+/?)',  # Labeled URL without protocol
    r'(?:linkedin|linked\s*in)[:\s]+([a-zA-Z0-9_-]{3,50})',  # Just the username after "linkedin:"
    r'linkedin\.com/in/([a-zA-Z0-9_-]+)',  # Extract username from URL
    r'(?:^|\s)(linkedin\.com/in/[a-zA-Z0-9_-]+)',  # URL without protocol at start or after space
]

GITHUB_PATTERNS = [
    r'(https?://(?:www\.)?github\.com/[a-zA-Z0-9_-]+/?)',  # Complete URL
    r'(?:github|git\s*hub)[:\s]+(https?://(?:www\.)?github\.com/[a-zA-Z0-9_-]+/?)',  # Labeled complete URL
    r'(?:github|git\s*hub)[:\s]+((?:www\.)?github\.com/[a-zA-Z0-9_-]+/?)',  # Labeled URL without protocol
    r'(?:github|git\s*hub)[:\s]+([a-zA-Z0-9_-]{1,39})',  # Just the username after "github:"
    r'github\.com/([a-zA-Z0-9_-]+)',  # Extract username from URL
    r'(?:^|\s)(github\.com/[a-zA-Z0-9_-]+)',  # URL without protocol at start or after space
]

# Lists of common skills for better detection
TECHNICAL_SKILLS = [
    "Python", "JavaScript", "Java", "C++", "C#", "PHP", "Ruby", "Swift", "Kotlin", "Go",
    "React", "Angular", "Vue.js", "Node.js", "Express", "Django", "Flask", "Spring Boot",
    "HTML", "CSS", "SQL", "MongoDB", "PostgreSQL", "MySQL", "Oracle", "Firebase",
    "AWS", "Azure", "Google Cloud", "Docker", "Kubernetes", "Git", "CI/CD",
    "Machine Learning", "Data Science", "Artificial Intelligence", "Deep Learning",
    "TensorFlow", "PyTorch", "Pandas", "NumPy", "Scikit-learn", "R", "Tableau", "Power BI",
    "Mobile Development", "Web Development", "Full Stack", "Frontend", "Backend", "DevOps",
    "Agile", "Scrum", "REST API", "GraphQL", "Microservices", "Linux", "Windows",
    "Networking", "Security", "Blockchain", "IoT", "AR/VR", "Game Development",
    "Solidity", "Ethereum", "Smart Contracts", "Web3", "DApp", "Hardhat", "Truffle",
    "Project Management", "Digital Marketing", "Teamwork", "Time Management", "Leadership",
    "Effective Communication", "Critical Thinking", "Problem Solving", "Analytical Skills"
]

SOFT_SKILLS = [
    "Communication", "Leadership", "Teamwork", "Problem Solving", "Critical Thinking",
    "Time Management", "Adaptability", "Creativity", "Project Management", "Collaboration",
    "Attention to Detail", "Organization", "Analytical Skills", "Interpersonal Skills",
    "Presentation Skills", "Negotiation", "Conflict Resolution", "Decision Making",
    "Emotional Intelligence", "Flexibility"
]

# Function to normalize and clean data for comparison
def normalize_text(text):
    if not text or not isinstance(text, str):
        return ""
    if text.strip().lower().startswith("contact "):
        text = text.strip()[8:].strip()
    return re.sub(r'\s+', ' ', text.strip().lower())

def normalize_phone(phone):
    if not phone or not isinstance(phone, str):
        return ""
    return re.sub(r'\D', '', phone.strip())

def normalize_email(email):
    if not email or not isinstance(email, str):
        return ""
    return email.strip().lower()

# Function to find and delete all duplicates
def find_and_delete_duplicates(resume_data):
    raw_name = resume_data.get("Full Name", "").strip()
    raw_email = resume_data.get("Email Address", "").strip()
    raw_phone = resume_data.get("Contact Number", "").strip()
    
    normalized_name = normalize_text(raw_name)
    normalized_email = normalize_email(raw_email)
    normalized_phone = normalize_phone(raw_phone)
    
    print(f"\n=== DUPLICATE DETECTION (ANY FIELD MATCH) ===")
    print(f"Looking for duplicates of:")
    print(f"  Raw Name: '{raw_name}' -> Normalized: '{normalized_name}'")
    print(f"  Raw Email: '{raw_email}' -> Normalized: '{normalized_email}'")
    print(f"  Raw Phone: '{raw_phone}' -> Normalized: '{normalized_phone}'")
    
    if not normalized_name and not normalized_email and not normalized_phone:
        print("No identifying information found - cannot check for duplicates")
        return 0, []
    
    deleted_count = 0
    duplicates_info = []
    
    try:
        if mongodb_available:
            all_resumes = list(resumes_collection.find())
            print(f"Found {len(all_resumes)} total resumes in database")
            
            duplicates_to_delete = []
            
            for existing_resume in all_resumes:
                existing_raw_name = existing_resume.get("Full Name", "").strip()
                existing_raw_email = existing_resume.get("Email Address", "").strip()
                existing_raw_phone = existing_resume.get("Contact Number", "").strip()
                
                existing_normalized_name = normalize_text(existing_raw_name)
                existing_normalized_email = normalize_email(existing_raw_email)
                existing_normalized_phone = normalize_phone(existing_raw_phone)
                
                name_match = normalized_name and existing_normalized_name and normalized_name == existing_normalized_name
                email_match = normalized_email and existing_normalized_email and normalized_email == existing_normalized_email
                phone_match = normalized_phone and existing_normalized_phone and normalized_phone == existing_normalized_phone
                
                is_duplicate = False
                match_reason = ""
                
                if name_match:
                    is_duplicate = True
                    match_reason = "name"
                elif email_match:
                    is_duplicate = True
                    match_reason = "email"
                elif phone_match:
                    is_duplicate = True
                    match_reason = "phone"
                
                if is_duplicate:
                    duplicates_to_delete.append(existing_resume["_id"])
                    duplicates_info.append({
                        "id": str(existing_resume.get("_id")),
                        "filename": existing_resume.get("filename", "Unknown"),
                        "name": existing_raw_name,
                        "email": existing_raw_email,
                        "phone": existing_raw_phone,
                        "match_reason": match_reason
                    })
                    print(f"  DUPLICATE FOUND: {existing_raw_name} (matched on: {match_reason})")
            
            if duplicates_to_delete:
                result = resumes_collection.delete_many({"_id": {"$in": duplicates_to_delete}})
                deleted_count = result.deleted_count
                print(f"Successfully deleted {deleted_count} duplicate records from MongoDB")
                
                for dup in duplicates_info:
                    print(f"  - Deleted: {dup['name']} ({dup['email']}, {dup['phone']}) - Matched on: {dup['match_reason']}")
            else:
                print("No duplicates found")
        
        else:
            # Handle in-memory storage
            to_remove = []
            for i, existing_resume in enumerate(resumes_data):
                existing_raw_name = existing_resume.get("Full Name", "").strip()
                existing_raw_email = existing_resume.get("Email Address", "").strip()
                existing_raw_phone = existing_resume.get("Contact Number", "").strip()
                
                existing_normalized_name = normalize_text(existing_raw_name)
                existing_normalized_email = normalize_email(existing_raw_email)
                existing_normalized_phone = normalize_phone(existing_raw_phone)
                
                name_match = normalized_name and existing_normalized_name and normalized_name == existing_normalized_name
                email_match = normalized_email and existing_normalized_email and normalized_email == existing_normalized_email
                phone_match = normalized_phone and existing_normalized_phone and normalized_phone == existing_normalized_phone
                
                is_duplicate = False
                match_reason = ""
                
                if name_match:
                    is_duplicate = True
                    match_reason = "name"
                elif email_match:
                    is_duplicate = True
                    match_reason = "email"
                elif phone_match:
                    is_duplicate = True
                    match_reason = "phone"
                
                if is_duplicate:
                    duplicates_info.append({
                        "id": existing_resume.get("_id", "Unknown"),
                        "filename": existing_resume.get("filename", "Unknown"),
                        "name": existing_raw_name,
                        "email": existing_raw_email,
                        "phone": existing_raw_phone,
                        "match_reason": match_reason
                    })
                    to_remove.append(i)
                    print(f"  DUPLICATE FOUND: {existing_raw_name} (matched on: {match_reason})")
            
            for i in reversed(to_remove):
                resumes_data.pop(i)
                deleted_count += 1
            
            print(f"Successfully deleted {deleted_count} duplicate records from in-memory storage")
            
            for dup in duplicates_info:
                print(f"  - Deleted: {dup['name']} ({dup['email']}, {dup['phone']}) - Matched on: {dup['match_reason']}")

    except Exception as e:
        print(f"Error finding and deleting duplicates: {e}")
        import traceback
        traceback.print_exc()

    print(f"=== DUPLICATE DETECTION COMPLETE ===\n")
    return deleted_count, duplicates_info

# Function to identify sections in the resume
def identify_sections(text):
    sections = {}
    lines = text.split('\n')
    current_section = 'header'
    sections[current_section] = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        section_found = False
        for section, patterns in SECTION_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, line, re.IGNORECASE) and len(line) < 50:
                    current_section = section
                    sections[current_section] = []
                    section_found = True
                    break
            if section_found:
                break
        
        sections[current_section].append(line)
    
    return sections

# Function to extract education details
def extract_education(education_section):
    education = []
    if not education_section:
        return education
    
    text = ' '.join(education_section)
    
    degree_patterns = [
        r'(?:bachelor|master|phd|b\.?s\.?|m\.?s\.?|b\.?e\.?|b\.?tech|m\.?tech)[^,\n]*',
        r'(?:degree)[^,\n]*'
    ]
    
    institution_patterns = [
        r'(?:university|college|institute|school)[^,\n]*'
    ]
    
    year_patterns = [
        r'(?:20\d{2})\s*-\s*(?:20\d{2}|present|ongoing)',
        r'(?:19\d{2})\s*-\s*(?:20\d{2}|present|ongoing)',
        r'(?:20\d{2})',
        r'(?:19\d{2})'
    ]
    
    field_patterns = [
        r'(?:in|of)\s+([A-Za-z\s&]+)'
    ]
    
    degrees = []
    for pattern in degree_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        degrees.extend(matches)
    
    institutions = []
    for pattern in institution_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        institutions.extend(matches)
    
    years = []
    for pattern in year_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        years.extend(matches)
    
    fields = []
    for pattern in field_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        fields.extend(matches)
    
    if degrees or institutions:
        if len(degrees) >= len(institutions):
            for i in range(len(degrees)):
                edu_entry = {
                    "Degree": degrees[i].strip() if i < len(degrees) else "",
                    "Institution": institutions[i].strip() if i < len(institutions) else "",
                    "Years": years[i].strip() if i < len(years) else "",
                    "Field": fields[i].strip() if i < len(fields) else ""
                }
                education.append(edu_entry)
        else:
            for i in range(len(institutions)):
                edu_entry = {
                    "Institution": institutions[i].strip(),
                    "Degree": degrees[i].strip() if i < len(degrees) else "",
                    "Years": years[i].strip() if i < len(years) else "",
                    "Field": fields[i].strip() if i < len(fields) else ""
                }
                education.append(edu_entry)
    
    if not education and education_section:
        for line in education_section:
            if len(line.strip()) > 10:
                if re.search(r'degree|bachelor|master|phd|university|college|institute|school', line, re.IGNORECASE):
                    degree = ""
                    institution = ""
                    years = ""
                    field = ""
                    
                    for pattern in degree_patterns:
                        degree_match = re.search(pattern, line, re.IGNORECASE)
                        if degree_match:
                            degree = degree_match.group(0).strip()
                            break
                    
                    for pattern in institution_patterns:
                        institution_match = re.search(pattern, line, re.IGNORECASE)
                        if institution_match:
                            institution = institution_match.group(0).strip()
                            break
                    
                    for pattern in year_patterns:
                        years_match = re.search(pattern, line, re.IGNORECASE)
                        if years_match:
                            years = years_match.group(0).strip()
                            break
                    
                    for pattern in field_patterns:
                        field_match = re.search(pattern, line, re.IGNORECASE)
                        if field_match:
                            field = field_match.group(1).strip()
                            break
                    
                    if degree or institution:
                        edu_entry = {
                            "Degree": degree,
                            "Institution": institution,
                            "Years": years,
                            "Field": field
                        }
                        education.append(edu_entry)
    
    return education

# Function to extract work experience
def extract_experience(experience_section):
    experience = []
    if not experience_section:
        return experience
    
    full_text = ' '.join(experience_section)
    
    job_entries = []
    current_entry = []
    
    for line in experience_section:
        if (re.search(r'(?:20\d{2}|19\d{2})\s*-\s*(?:20\d{2}|19\d{2}|present|ongoing)', line, re.IGNORECASE) or
            re.search(r'^[A-Z][a-zA-Z\s&]+,', line) or
            re.search(r'^[A-Z][a-zA-Z\s&]+\s+\|', line) or
            re.search(r'^(?:senior|junior|lead|principal|software|web|mobile|data|cloud|devops|full\s+stack|front\s*end|back\s*end)\s+', line, re.IGNORECASE)):
            
            if current_entry:
                job_entries.append('\n'.join(current_entry))
            
            current_entry = [line]
        else:
            current_entry.append(line)
    
    if current_entry:
        job_entries.append('\n'.join(current_entry))
    
    if not job_entries:
        job_entries = [full_text]
    
    for entry in job_entries:
        if len(entry.strip()) < 10:
            continue
        
        company = ""
        role = ""
        years = ""
        description = entry.strip()
        
        company_patterns = [
            r'(?:at|with|for)?\s*([A-Za-z0-9\s&.,]+?)(?:\n|\s{2,}|$)',
            r'(?:company|employer)[:\s]+([A-Za-z0-9\s&.,]+)',
            r'^([A-Z][a-zA-Z\s&]+),',
            r'^([A-Z][a-zA-Z\s&]+)\s+\|'
        ]
        
        for pattern in company_patterns:
            company_match = re.search(pattern, entry, re.IGNORECASE)
            if company_match:
                company = company_match.group(1).strip()
                break
        
        role_patterns = [
            r'(?:as|position|role|title)[:\s]+([A-Za-z\s]+)',
            r'(?:senior|junior|lead|principal|software|web|mobile|data|cloud|devops|full\s+stack|front\s*end|back\s*end)\s+([A-Za-z\s]+)',
            r'([A-Za-z\s]+?)\s+(?:developer|engineer|analyst|manager|consultant|designer|architect)'
        ]
        
        for pattern in role_patterns:
            role_match = re.search(pattern, entry, re.IGNORECASE)
            if role_match:
                role = role_match.group(1).strip()
                if not re.search(r'developer|engineer|analyst|manager|consultant|designer|architect', role, re.IGNORECASE):
                    suffix_match = re.search(r'(developer|engineer|analyst|manager|consultant|designer|architect)', entry, re.IGNORECASE)
                    if suffix_match:
                        role += " " + suffix_match.group(1)
                break
        
        year_patterns = [
            r'(?:20\d{2}|19\d{2})\s*-\s*(?:20\d{2}|19\d{2}|present|ongoing)',
            r'(?:from|since)\s+(?:20\d{2}|19\d{2})',
            r'(?:20\d{2}|19\d{2})'
        ]
        
        for pattern in year_patterns:
            years_match = re.search(pattern, entry, re.IGNORECASE)
            if years_match:
                years = years_match.group(0).strip()
                break
        
        if company or role:
            exp_entry = {
                "Company": company,
                "Role": role,
                "Years": years,
                "Description": description
            }
            experience.append(exp_entry)
    
    return experience

# Function to extract skills
def extract_skills(text, skills_section=None):
    technical_skills = []
    soft_skills = []
    
    if skills_section:
        skills_text = ' '.join(skills_section)
    else:
        skills_text = text
    
    for skill in TECHNICAL_SKILLS:
        if re.search(r'\b' + re.escape(skill) + r'\b', skills_text, re.IGNORECASE):
            if skill not in technical_skills:
                technical_skills.append(skill)
    
    for skill in SOFT_SKILLS:
        if re.search(r'\b' + re.escape(skill) + r'\b', skills_text, re.IGNORECASE):
            if skill not in soft_skills:
                soft_skills.append(skill)
    
    if not technical_skills and not skills_section:
        for skill in TECHNICAL_SKILLS:
            if re.search(r'\b' + re.escape(skill) + r'\b', text, re.IGNORECASE):
                if skill not in technical_skills:
                    technical_skills.append(skill)
    
    if not soft_skills and not skills_section:
        for skill in SOFT_SKILLS:
            if re.search(r'\b' + re.escape(skill) + r'\b', text, re.IGNORECASE):
                if skill not in soft_skills:
                    soft_skills.append(skill)
    
    return {
        "Technical": technical_skills,
        "Soft": soft_skills
    }

# Function to validate and format LinkedIn URL - IMPROVED
def validate_linkedin_url(url_or_username):
    if isinstance(url_or_username, str):
        url_or_username = url_or_username.strip()
        
        # If it's already a complete URL
        if url_or_username.startswith('http'):
            if '/in/' in url_or_username:
                return url_or_username
        
        # If it contains linkedin.com
        if 'linkedin.com' in url_or_username:
            match = re.search(r'linkedin\.com/in/([a-zA-Z0-9_-]+)', url_or_username)
            if match:
                return f"https://www.linkedin.com/in/{match.group(1)}"
        
        # If it's just a username (3-100 chars, alphanumeric, dash, underscore)
        if re.match(r'^[a-zA-Z0-9_-]{3,100}$', url_or_username):
            return f"https://www.linkedin.com/in/{url_or_username}"
    
    return ""

# Function to validate and format GitHub URL
def validate_github_url(url_or_username):
    if isinstance(url_or_username, str):
        url_or_username = url_or_username.strip()
        
        if url_or_username.startswith('http'):
            if 'github.com/' in url_or_username:
                return url_or_username
        
        if 'github.com' in url_or_username:
            match = re.search(r'github\.com/([a-zA-Z0-9_-]+)', url_or_username)
            if match:
                return f"https://github.com/{match.group(1)}"
        
        if re.match(r'^[a-zA-Z0-9_-]{1,39}$', url_or_username):
            return f"https://github.com/{url_or_username}"
    
    return ""

# Function to fetch profile data from LinkedIn and GitHub
def fetch_profile_data(linkedin_url, github_url):
    profile_data = {
        "linkedin": {},
        "github": {}
    }
    
    if linkedin_url:
        try:
            print(f"Would fetch LinkedIn profile data from: {linkedin_url}")
        except Exception as e:
            print(f"Error fetching LinkedIn profile: {e}")
    
    if github_url:
        try:
            username = re.search(r'github\.com/([a-zA-Z0-9_-]+)', github_url)
            if username:
                username = username.group(1)
                response = requests.get(f"https://api.github.com/users/{username}")
                if response.status_code == 200:
                    profile_data["github"] = response.json()
        except Exception as e:
            print(f"Error fetching GitHub profile: {e}")
    
    return profile_data

# Function to extract personal information - IMPROVED LinkedIn extraction
def extract_personal_info(text, personal_section=None):
    personal_info = {
        "Full Name": "",
        "Email Address": "",
        "Contact Number": "",
        "Location": "",
        "LinkedIn": "",
        "GitHub": ""
    }
    
    if personal_section:
        personal_text = '\n'.join(personal_section)
    else:
        lines = text.split('\n')
        personal_text = '\n'.join(lines[:20])
    
    # Extract name
    first_line = text.split('\n')[0].strip()
    if 5 <= len(first_line) <= 40 and not re.search(r'resume|cv|curriculum|vitae', first_line, re.IGNORECASE):
        words = first_line.split()
        if 1 < len(words) <= 5:
            if all(word[0].isupper() for word in words if len(word) > 1):
                name = first_line
                if name.startswith("Contact "):
                    name = name[8:].strip()
                personal_info["Full Name"] = name
    
    if not personal_info["Full Name"]:
        for pattern in NAME_PATTERNS:
            try:
                matches = re.findall(pattern, personal_text, re.MULTILINE)
                if matches:
                    name = matches[0].strip()
                    if name.startswith("Contact "):
                        name = name[8:].strip()
                    personal_info["Full Name"] = name
                    break
            except Exception as e:
                print(f"Error with name pattern '{pattern}': {e}")
                continue
    
    if not personal_info["Full Name"] and personal_section:
        for pattern in NAME_PATTERNS:
            try:
                matches = re.findall(pattern, text, re.MULTILINE)
                if matches:
                    name = matches[0].strip()
                    if name.startswith("Contact "):
                        name = name[8:].strip()
                    personal_info["Full Name"] = name
                    break
            except Exception as e:
                print(f"Error with name pattern '{pattern}': {e}")
                continue
    
    # Extract email
    for pattern in EMAIL_PATTERNS:
        try:
            matches = re.findall(pattern, personal_text)
            if matches:
                if isinstance(matches[0], tuple):
                    personal_info["Email Address"] = matches[0][0].strip()
                else:
                    personal_info["Email Address"] = matches[0].strip()
                break
        except Exception as e:
            print(f"Error with email pattern '{pattern}': {e}")
            continue
    
    if not personal_info["Email Address"]:
        for pattern in EMAIL_PATTERNS:
            try:
                matches = re.findall(pattern, text)
                if matches:
                    if isinstance(matches[0], tuple):
                        personal_info["Email Address"] = matches[0][0].strip()
                    else:
                        personal_info["Email Address"] = matches[0].strip()
                    break
            except Exception as e:
                print(f"Error with email pattern '{pattern}': {e}")
                continue
    
    # Extract phone number
    for pattern in PHONE_PATTERNS:
        try:
            matches = re.findall(pattern, personal_text)
            if matches:
                if isinstance(matches[0], tuple):
                    personal_info["Contact Number"] = matches[0][0].strip()
                else:
                    personal_info["Contact Number"] = matches[0].strip()
                break
        except Exception as e:
            print(f"Error with phone pattern '{pattern}': {e}")
            continue
    
    if not personal_info["Contact Number"]:
        for pattern in PHONE_PATTERNS:
            try:
                matches = re.findall(pattern, text)
                if matches:
                    if isinstance(matches[0], tuple):
                        personal_info["Contact Number"] = matches[0][0].strip()
                    else:
                        personal_info["Contact Number"] = matches[0].strip()
                    break
            except Exception as e:
                print(f"Error with phone pattern '{pattern}': {e}")
                continue
    
    # Extract location
    for pattern in LOCATION_PATTERNS:
        try:
            matches = re.findall(pattern, personal_text, re.IGNORECASE)
            if matches:
                personal_info["Location"] = matches[0].strip()
                break
        except Exception as e:
            print(f"Error with location pattern '{pattern}': {e}")
            continue
    
    if not personal_info["Location"]:
        for pattern in LOCATION_PATTERNS:
            try:
                matches = re.findall(pattern, text, re.IGNORECASE)
                if matches:
                    personal_info["Location"] = matches[0].strip()
                    break
            except Exception as e:
                print(f"Error with location pattern '{pattern}': {e}")
                continue
    
    # Extract LinkedIn URL - IMPROVED
    for pattern in LINKEDIN_PATTERNS:
        try:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                url = matches[0]
                validated_url = validate_linkedin_url(url)
                if validated_url:
                    personal_info["LinkedIn"] = validated_url
                    break
        except Exception as e:
            print(f"Error with LinkedIn pattern '{pattern}': {e}")
            continue
    
    # Extract GitHub URL
    for pattern in GITHUB_PATTERNS:
        try:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                url = matches[0]
                validated_url = validate_github_url(url)
                if validated_url:
                    personal_info["GitHub"] = validated_url
                    break
        except Exception as e:
            print(f"Error with GitHub pattern '{pattern}': {e}")
            continue
    
    # Fetch additional profile data
    if personal_info["LinkedIn"] or personal_info["GitHub"]:
        profile_data = fetch_profile_data(personal_info["LinkedIn"], personal_info["GitHub"])
        personal_info["LinkedInData"] = profile_data["linkedin"]
        personal_info["GitHubData"] = profile_data["github"]
    
    return personal_info

# Function to generate recommended roles based on skills
def generate_recommended_roles(skills):
    role_mapping = {
        "Python": ["Python Developer", "Data Scientist", "Backend Developer"],
        "JavaScript": ["Frontend Developer", "Full Stack Developer", "Web Developer"],
        "React": ["React Developer", "Frontend Developer", "UI Developer"],
        "Angular": ["Angular Developer", "Frontend Developer", "UI Developer"],
        "Node.js": ["Node.js Developer", "Backend Developer", "Full Stack Developer"],
        "SQL": ["Database Administrator", "Data Analyst", "Backend Developer"],
        "MongoDB": ["MongoDB Developer", "NoSQL Developer", "Backend Developer"],
        "AWS": ["Cloud Engineer", "DevOps Engineer", "Solutions Architect"],
        "Docker": ["DevOps Engineer", "Cloud Engineer", "Systems Administrator"],
        "Machine Learning": ["Machine Learning Engineer", "Data Scientist", "AI Researcher"],
        "Full Stack": ["Full Stack Developer", "Web Developer", "Software Engineer"],
        "Frontend": ["Frontend Developer", "UI Developer", "Web Designer"],
        "Backend": ["Backend Developer", "API Developer", "Server Engineer"],
        "Mobile": ["Mobile Developer", "iOS Developer", "Android Developer"],
        "DevOps": ["DevOps Engineer", "SRE", "Cloud Engineer"],
        "Data": ["Data Analyst", "Data Engineer", "Business Intelligence Analyst"],
        "Solidity": ["Blockchain Developer", "Smart Contract Developer", "Ethereum Developer"],
        "Ethereum": ["Blockchain Developer", "Smart Contract Developer", "DApp Developer"],
        "Smart Contracts": ["Blockchain Developer", "Smart Contract Developer", "Solidity Developer"],
        "Web3": ["Blockchain Developer", "DApp Developer", "Web3 Developer"]
    }
    
    recommended_roles = set()
    for skill in skills.get("Technical", []):
        for key, roles in role_mapping.items():
            if key.lower() in skill.lower() or skill.lower() in key.lower():
                for role in roles:
                    recommended_roles.add(role)
    
    return list(recommended_roles)[:5]

# Function to score a resume based on a search skill
def score_resume(resume, search_skill):
    score = 0
    search_skill_lower = search_skill.lower()
    
    if 'Skills' in resume:
        all_skills = []
        
        if isinstance(resume['Skills'], dict):
            technical_skills = resume['Skills'].get('Technical', [])
            if isinstance(technical_skills, list):
                all_skills.extend(technical_skills)
            
            soft_skills = resume['Skills'].get('Soft', [])
            if isinstance(soft_skills, list):
                all_skills.extend(soft_skills)
        elif isinstance(resume['Skills'], list):
            all_skills = resume['Skills']
        elif isinstance(resume['Skills'], str):
            all_skills = [resume['Skills']]
        
        for skill in all_skills:
            if isinstance(skill, str) and search_skill_lower == skill.lower():
                score += 40
                break
        
        if score == 0:
            for skill in all_skills:
                if isinstance(skill, str):
                    skill_lower = skill.lower()
                    if search_skill_lower in skill_lower or skill_lower in search_skill_lower:
                        overlap_length = min(len(search_skill_lower), len(skill_lower))
                        score += min(30, overlap_length * 2)
                        break
    
    if 'Work Experience' in resume and isinstance(resume['Work Experience'], list):
        experience_score = 0
        for job in resume['Work Experience']:
            if not isinstance(job, dict):
                continue
                
            role = job.get('Role', '') if isinstance(job.get('Role', ''), str) else ''
            description = job.get('Description', '') if isinstance(job.get('Description', ''), str) else ''
            
            role_lower = role.lower()
            description_lower = description.lower()
            
            if search_skill_lower in role_lower or search_skill_lower in description_lower:
                years_text = job.get('Years', '') if isinstance(job.get('Years', ''), str) else ''
                years = 0
                
                if '-' in years_text:
                    try:
                        start, end = years_text.split('-')
                        start_year_match = re.search(r'\d{4}', start)
                        if start_year_match:
                            start_year = int(start_year_match.group(0))
                            
                            if 'present' in end.lower() or 'ongoing' in end.lower():
                                end_year = datetime.now().year
                            else:
                                end_year_match = re.search(r'\d{4}', end)
                                if end_year_match:
                                    end_year = int(end_year_match.group(0))
                                else:
                                    end_year = start_year + 1
                            
                            years = end_year - start_year
                    except:
                        years = 1
                elif re.search(r'\d+', years_text):
                    try:
                        years = int(re.search(r'\d+', years_text).group(0))
                    except:
                        years = 1
                else:
                    years = 1
                
                experience_score += min(15, years * 3)
                
                if search_skill_lower in role_lower:
                    experience_score += 15
        
        score += min(30, experience_score)
    
    if 'Projects' in resume and isinstance(resume['Projects'], list):
        for project in resume['Projects']:
            if not isinstance(project, dict):
                continue
                
            project_name = project.get('Name', '') if isinstance(project.get('Name', ''), str) else ''
            project_description = project.get('Description', '') if isinstance(project.get('Description', ''), str) else ''
            
            project_name_lower = project_name.lower()
            project_description_lower = project_description.lower()
            
            if search_skill_lower in project_name_lower or search_skill_lower in project_description_lower:
                score += 15
                break
    
    if 'Education' in resume and isinstance(resume['Education'], list):
        for edu in resume['Education']:
            if not isinstance(edu, dict):
                continue
                
            degree = edu.get('Degree', '') if isinstance(edu.get('Degree', ''), str) else ''
            field = edu.get('Field', '') if isinstance(edu.get('Field', ''), str) else ''
            
            degree_lower = degree.lower()
            field_lower = field.lower()
            
            if search_skill_lower in degree_lower or search_skill_lower in field_lower:
                score += 10
                break
    
    if 'Certifications' in resume and isinstance(resume['Certifications'], list):
        for cert in resume['Certifications']:
            if isinstance(cert, str) and search_skill_lower in cert.lower():
                score += 5
                break
    
    return score

# Improved resume parsing function
def parse_resume(text, filename=""):
    if gemini_available:
        try:
            ai_resume_data = parse_resume_with_ai(text)
            
            if ai_resume_data and not "error" in ai_resume_data:
                if "Full Name" in ai_resume_data and ai_resume_data["Full Name"]:
                    name = ai_resume_data["Full Name"]
                    if name.startswith("Contact "):
                        ai_resume_data["Full Name"] = name[8:].strip()
                
                ai_resume_data["upload_date"] = datetime.now()
                ai_resume_data["filename"] = filename
                
                # Generate CV URL
                cv_url, cv_filename = generate_cv_url(filename)
                ai_resume_data["cv_url"] = cv_url
                ai_resume_data["cv_filename"] = cv_filename
                
                if "Skills" not in ai_resume_data:
                    ai_resume_data["Skills"] = {"Technical": [], "Soft": []}
                elif not isinstance(ai_resume_data["Skills"], dict):
                    ai_resume_data["Skills"] = {"Technical": ai_resume_data["Skills"] if isinstance(ai_resume_data["Skills"], list) else [], "Soft": []}
                
                if "Education" not in ai_resume_data:
                    ai_resume_data["Education"] = []
                
                if "Work Experience" not in ai_resume_data:
                    ai_resume_data["Work Experience"] = []
                
                if "Recommended Roles" not in ai_resume_data:
                    ai_resume_data["Recommended Roles"] = generate_recommended_roles(ai_resume_data["Skills"])
                
                sections = identify_sections(text)
                if 'projects' in sections:
                    projects = []
                    project_lines = sections['projects']
                    current_project = {"Name": "", "Description": ""}
                    
                    for line in project_lines:
                        if re.match(r'^[A-Z]', line) and len(line) < 50:
                            if current_project["Name"]:
                                projects.append(current_project)
                                current_project = {"Name": "", "Description": ""}
                            current_project["Name"] = line
                        else:
                            current_project["Description"] += line + " "
                    
                    if current_project["Name"]:
                        projects.append(current_project)
                    
                    ai_resume_data["Projects"] = projects
                else:
                    ai_resume_data["Projects"] = []
                
                if "LinkedIn" in ai_resume_data and ai_resume_data["LinkedIn"]:
                    ai_resume_data["LinkedIn"] = validate_linkedin_url(ai_resume_data["LinkedIn"])
                
                if "GitHub" in ai_resume_data and ai_resume_data["GitHub"]:
                    ai_resume_data["GitHub"] = validate_github_url(ai_resume_data["GitHub"])
                
                if ai_resume_data.get("LinkedIn") or ai_resume_data.get("GitHub"):
                    profile_data = fetch_profile_data(
                        ai_resume_data.get("LinkedIn", ""), 
                        ai_resume_data.get("GitHub", "")
                    )
                    ai_resume_data["LinkedInData"] = profile_data["linkedin"]
                    ai_resume_data["GitHubData"] = profile_data["github"]
                
                return ai_resume_data
            
        except Exception as e:
            print(f"Error parsing resume with AI: {e}")
    
    # Rule-based parsing fallback
    resume_data = {
        "Full Name": "",
        "Email Address": "",
        "Contact Number": "",
        "Location": "",
        "LinkedIn": "",
        "GitHub": "",
        "LinkedInData": {},
        "GitHubData": {},
        "Skills": {
            "Technical": [],
            "Soft": []
        },
        "Education": [],
        "Work Experience": [],
        "Projects": [],
        "Recommended Roles": [],
        "upload_date": datetime.now(),
        "filename": filename
    }
    
    # Generate CV URL
    cv_url, cv_filename = generate_cv_url(filename)
    resume_data["cv_url"] = cv_url
    resume_data["cv_filename"] = cv_filename
    
    sections = identify_sections(text)
    
    personal_info = extract_personal_info(text, sections.get('personal_info', None))
    resume_data.update(personal_info)
    
    resume_data["Education"] = extract_education(sections.get('education', None))
    resume_data["Work Experience"] = extract_experience(sections.get('experience', None))
    resume_data["Skills"] = extract_skills(text, sections.get('skills', None))
    
    if 'projects' in sections:
        projects = []
        project_lines = sections['projects']
        current_project = {"Name": "", "Description": ""}
        
        for line in project_lines:
            if re.match(r'^[A-Z]', line) and len(line) < 50:
                if current_project["Name"]:
                    projects.append(current_project)
                    current_project = {"Name": "", "Description": ""}
                current_project["Name"] = line
            else:
                current_project["Description"] += line + " "
        
        if current_project["Name"]:
            projects.append(current_project)
        
        resume_data["Projects"] = projects
    
    resume_data["Recommended Roles"] = generate_recommended_roles(resume_data["Skills"])
    
    return resume_data

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.html')

@app.route('/form')
def form():
    return render_template('form.html')

@app.route('/upload_resume', methods=['POST'])
def upload_resume():
    if 'resume' not in request.files:
        return jsonify({'error': 'No file uploaded'})
    
    file = request.files['resume']
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'})
    
    if file and allowed_file(file.filename):
        try:
            filename = str(uuid.uuid4()) + '_' + secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            
            file.save(file_path)
            
            text = extract_text(file_path)
            resume_data = parse_resume(text, file.filename)
            
            # Save CV file for URL access
            cv_file_path = os.path.join(app.config['CV_FOLDER'], resume_data["cv_filename"])
            import shutil
            shutil.copy2(file_path, cv_file_path)
            
            deleted_count, duplicates_info = find_and_delete_duplicates(resume_data)
            
            try:
                if mongodb_available:
                    resume_id = resumes_collection.insert_one(resume_data).inserted_id
                    resume_data['_id'] = str(resume_id)
                else:
                    resume_data['_id'] = str(uuid.uuid4())
                    resumes_data.append(resume_data)
            except Exception as e:
                print(f"MongoDB error: {e}")
                resume_data['_id'] = str(uuid.uuid4())
                resumes_data.append(resume_data)
            
            os.remove(file_path)
            
            return jsonify({
                'success': True,
                'resume_data': resume_data,
                'duplicates_removed': deleted_count,
                'duplicates_info': duplicates_info
            })
            
        except Exception as e:
            print(f"Error processing file {file.filename}: {e}")
            return jsonify({'error': f'Error processing file: {str(e)}'})
    else:
        return jsonify({'error': 'File type not allowed'})

@app.route('/upload_resumes', methods=['POST'])
def upload_resumes():
    if 'resumes' not in request.files:
        return jsonify({'error': 'No files uploaded'})
    
    files = request.files.getlist('resumes')
    
    if not files or all(file.filename == '' for file in files):
        return jsonify({'error': 'No files selected'})
    
    processed_resumes = []
    failed_files = []
    all_duplicates_info = []
    total_deleted = 0
    
    for file in files:
        if file and allowed_file(file.filename):
            try:
                filename = str(uuid.uuid4()) + '_' + secure_filename(file.filename)
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                
                file.save(file_path)
                
                text = extract_text(file_path)
                resume_data = parse_resume(text, file.filename)
                
                # Save CV file for URL access
                cv_file_path = os.path.join(app.config['CV_FOLDER'], resume_data["cv_filename"])
                import shutil
                shutil.copy2(file_path, cv_file_path)
                
                print(f"\nProcessing: {file.filename}")
                print(f"Extracted data - Name: {resume_data.get('Full Name', 'N/A')}, Email: {resume_data.get('Email Address', 'N/A')}, Phone: {resume_data.get('Contact Number', 'N/A')}")
                
                deleted_count, duplicates_info = find_and_delete_duplicates(resume_data)
                total_deleted += deleted_count
                all_duplicates_info.extend(duplicates_info)
                
                if deleted_count > 0:
                    print(f"Found and removed {deleted_count} duplicate(s) for {file.filename}")
                else:
                    print(f"No duplicates found for {file.filename}")
                
                try:
                    if mongodb_available:
                        resume_id = resumes_collection.insert_one(resume_data).inserted_id
                        resume_data['_id'] = str(resume_id)
                        print(f"Saved to MongoDB with ID: {resume_id}")
                    else:
                        resume_data['_id'] = str(uuid.uuid4())
                        resumes_data.append(resume_data)
                        print(f"Saved to in-memory storage with ID: {resume_data['_id']}")
                except Exception as e:
                    print(f"MongoDB error: {e}")
                    resume_data['_id'] = str(uuid.uuid4())
                    resumes_data.append(resume_data)
                    print(f"Fallback: Saved to in-memory storage with ID: {resume_data['_id']}")
                
                processed_resumes.append(resume_data)
                
                os.remove(file_path)
                
            except Exception as e:
                print(f"Error processing file {file.filename}: {e}")
                import traceback
                traceback.print_exc()
                failed_files.append(file.filename)
        else:
            failed_files.append(file.filename)
    
    print(f"\nSummary:")
    print(f"- Processed: {len(processed_resumes)} resumes")
    print(f"- Failed: {len(failed_files)} files")
    print(f"- Total duplicates removed: {total_deleted}")
    
    return jsonify({
        'success': True,
        'processed_count': len(processed_resumes),
        'failed_count': len(failed_files),
        'duplicate_count': total_deleted,
        'processed_resumes': processed_resumes,
        'failed_files': failed_files,
        'duplicates': all_duplicates_info
    })

@app.route('/get_resumes', methods=['GET'])
def get_resumes():
    try:
        if mongodb_available:
            resumes = list(resumes_collection.find().sort('upload_date', -1))
            for resume in resumes:
                resume['_id'] = str(resume['_id'])
        else:
            resumes = resumes_data
    except Exception as e:
        print(f"MongoDB error: {e}")
        resumes = resumes_data
    
    return jsonify(resumes)

@app.route('/get_resume/<resume_id>', methods=['GET'])
def get_resume(resume_id):
    try:
        if mongodb_available:
            from bson.objectid import ObjectId
            try:
                resume = resumes_collection.find_one({'_id': ObjectId(resume_id)})
                if resume:
                    resume['_id'] = str(resume['_id'])
                    return jsonify(resume)
            except Exception as e:
                print(f"MongoDB error: {e}")
        
        for resume in resumes_data:
            if resume['_id'] == resume_id:
                return jsonify(resume)
    except Exception as e:
        print(f"Error retrieving resume: {e}")
    
    return jsonify({'error': 'Resume not found'})

@app.route('/search_resumes', methods=['GET'])
def search_resumes():
    skill = request.args.get('skill', '')
    if not skill:
        return jsonify({'error': 'No skill provided'})
    
    try:
        if mongodb_available:
            resumes = list(resumes_collection.find())
            for resume in resumes:
                resume['_id'] = str(resume['_id'])
        else:
            resumes = resumes_data
        
        scored_resumes = []
        for resume in resumes:
            has_skill = False
            if 'Skills' in resume:
                all_skills = []
                
                if isinstance(resume['Skills'], dict):
                    technical_skills = resume['Skills'].get('Technical', [])
                    if isinstance(technical_skills, list):
                        all_skills.extend(technical_skills)
                    
                    soft_skills = resume['Skills'].get('Soft', [])
                    if isinstance(soft_skills, list):
                        all_skills.extend(soft_skills)
                elif isinstance(resume['Skills'], list):
                    all_skills = resume['Skills']
                elif isinstance(resume['Skills'], str):
                    all_skills = [resume['Skills']]
                
                skill_lower = skill.lower()
                for resume_skill in all_skills:
                    if isinstance(resume_skill, str):
                        resume_skill_lower = resume_skill.lower()
                        if skill_lower == resume_skill_lower or skill_lower in resume_skill_lower or resume_skill_lower in skill_lower:
                            has_skill = True
                            break
            
            if has_skill:
                score = score_resume(resume, skill)
                resume_copy = resume.copy()
                resume_copy['score'] = score
                scored_resumes.append(resume_copy)
        
        scored_resumes.sort(key=lambda x: x['score'], reverse=True)
        
        for i, resume in enumerate(scored_resumes):
            resume['rank'] = i + 1
            resume['rank_label'] = f"#{i+1}"
        
        return jsonify(scored_resumes)
    
    except Exception as e:
        print(f"Error searching resumes: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Error searching resumes: {e}'})

# NEW ROUTES FOR ADVANCED FILTERING AND CSV EXPORT

@app.route('/get_filter_options', methods=['GET'])
def get_filter_options():
    """Get all unique values for filtering options"""
    try:
        if mongodb_available:
            resumes = list(resumes_collection.find())
            for resume in resumes:
                resume['_id'] = str(resume['_id'])
        else:
            resumes = resumes_data
        
        # Extract unique values for each filter category
        locations = set()
        technical_skills = set()
        soft_skills = set()
        companies = set()
        roles = set()
        degrees = set()
        institutions = set()
        
        for resume in resumes:
            # Location
            if resume.get('Location'):
                locations.add(resume['Location'])
            
            # Skills
            if resume.get('Skills'):
                if isinstance(resume['Skills'], dict):
                    tech_skills = resume['Skills'].get('Technical', [])
                    if isinstance(tech_skills, list):
                        technical_skills.update(tech_skills)
                    
                    soft_skills_list = resume['Skills'].get('Soft', [])
                    if isinstance(soft_skills_list, list):
                        soft_skills.update(soft_skills_list)
            
            # Work Experience
            if resume.get('Work Experience') and isinstance(resume['Work Experience'], list):
                for exp in resume['Work Experience']:
                    if isinstance(exp, dict):
                        if exp.get('Company'):
                            companies.add(exp['Company'])
                        if exp.get('Role'):
                            roles.add(exp['Role'])
            
            # Education
            if resume.get('Education') and isinstance(resume['Education'], list):
                for edu in resume['Education']:
                    if isinstance(edu, dict):
                        if edu.get('Degree'):
                            degrees.add(edu['Degree'])
                        if edu.get('Institution'):
                            institutions.add(edu['Institution'])
        
        return jsonify({
            'locations': sorted(list(locations)),
            'technical_skills': sorted(list(technical_skills)),
            'soft_skills': sorted(list(soft_skills)),
            'companies': sorted(list(companies)),
            'roles': sorted(list(roles)),
            'degrees': sorted(list(degrees)),
            'institutions': sorted(list(institutions))
        })
    
    except Exception as e:
        print(f"Error getting filter options: {e}")
        return jsonify({'error': f'Error getting filter options: {e}'})

@app.route('/filter_resumes', methods=['POST'])
def filter_resumes():
    """Filter resumes based on multiple criteria"""
    try:
        filters = request.json
        
        if mongodb_available:
            resumes = list(resumes_collection.find())
            for resume in resumes:
                resume['_id'] = str(resume['_id'])
        else:
            resumes = resumes_data
        
        filtered_resumes = []
        
        for resume in resumes:
            matches = True
            
            # Filter by location
            if filters.get('locations') and resume.get('Location'):
                if resume['Location'] not in filters['locations']:
                    matches = False
                    continue
            
            # Filter by technical skills
            if filters.get('technical_skills'):
                resume_tech_skills = []
                if resume.get('Skills') and isinstance(resume['Skills'], dict):
                    tech_skills = resume['Skills'].get('Technical', [])
                    if isinstance(tech_skills, list):
                        resume_tech_skills = tech_skills
                
                if not any(skill in resume_tech_skills for skill in filters['technical_skills']):
                    matches = False
                    continue
            
            # Filter by soft skills
            if filters.get('soft_skills'):
                resume_soft_skills = []
                if resume.get('Skills') and isinstance(resume['Skills'], dict):
                    soft_skills_list = resume['Skills'].get('Soft', [])
                    if isinstance(soft_skills_list, list):
                        resume_soft_skills = soft_skills_list
                
                if not any(skill in resume_soft_skills for skill in filters['soft_skills']):
                    matches = False
                    continue
            
            # Filter by companies
            if filters.get('companies'):
                resume_companies = []
                if resume.get('Work Experience') and isinstance(resume['Work Experience'], list):
                    for exp in resume['Work Experience']:
                        if isinstance(exp, dict) and exp.get('Company'):
                            resume_companies.append(exp['Company'])
                
                if not any(company in resume_companies for company in filters['companies']):
                    matches = False
                    continue
            
            # Filter by roles
            if filters.get('roles'):
                resume_roles = []
                if resume.get('Work Experience') and isinstance(resume['Work Experience'], list):
                    for exp in resume['Work Experience']:
                        if isinstance(exp, dict) and exp.get('Role'):
                            resume_roles.append(exp['Role'])
                
                if not any(role in resume_roles for role in filters['roles']):
                    matches = False
                    continue
            
            # Filter by degrees
            if filters.get('degrees'):
                resume_degrees = []
                if resume.get('Education') and isinstance(resume['Education'], list):
                    for edu in resume['Education']:
                        if isinstance(edu, dict) and edu.get('Degree'):
                            resume_degrees.append(edu['Degree'])
                
                if not any(degree in resume_degrees for degree in filters['degrees']):
                    matches = False
                    continue
            
            # Filter by institutions
            if filters.get('institutions'):
                resume_institutions = []
                if resume.get('Education') and isinstance(resume['Education'], list):
                    for edu in resume['Education']:
                        if isinstance(edu, dict) and edu.get('Institution'):
                            resume_institutions.append(edu['Institution'])
                
                if not any(institution in resume_institutions for institution in filters['institutions']):
                    matches = False
                    continue
            
            if matches:
                filtered_resumes.append(resume)
        
        return jsonify(filtered_resumes)
    
    except Exception as e:
        print(f"Error filtering resumes: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Error filtering resumes: {e}'})

@app.route('/export_csv', methods=['POST'])
def export_csv():
    """Export filtered resumes to CSV format"""
    try:
        resumes = request.json.get('resumes', [])
        
        if not resumes:
            return jsonify({'error': 'No resumes provided for export'})
        
        # Create CSV content
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write header
        headers = [
            'ID', 'Full Name', 'Email Address', 'Contact Number', 'Location',
            'LinkedIn', 'GitHub', 'CV URL', 'Technical Skills', 'Soft Skills',
            'Education', 'Work Experience', 'Projects', 'Recommended Roles',
            'Upload Date', 'Filename'
        ]
        writer.writerow(headers)
        
        # Write data rows
        for resume in resumes:
            # Format skills
            tech_skills = ''
            soft_skills = ''
            if resume.get('Skills'):
                if isinstance(resume['Skills'], dict):
                    tech_skills = ', '.join(resume['Skills'].get('Technical', []))
                    soft_skills = ', '.join(resume['Skills'].get('Soft', []))
            
            # Format education
            education = ''
            if resume.get('Education') and isinstance(resume['Education'], list):
                edu_list = []
                for edu in resume['Education']:
                    if isinstance(edu, dict):
                        edu_str = f"{edu.get('Degree', '')} from {edu.get('Institution', '')} ({edu.get('Years', '')})"
                        edu_list.append(edu_str.strip())
                education = '; '.join(edu_list)
            
            # Format work experience
            work_exp = ''
            if resume.get('Work Experience') and isinstance(resume['Work Experience'], list):
                exp_list = []
                for exp in resume['Work Experience']:
                    if isinstance(exp, dict):
                        exp_str = f"{exp.get('Role', '')} at {exp.get('Company', '')} ({exp.get('Years', '')})"
                        exp_list.append(exp_str.strip())
                work_exp = '; '.join(exp_list)
            
            # Format projects
            projects = ''
            if resume.get('Projects') and isinstance(resume['Projects'], list):
                proj_list = []
                for proj in resume['Projects']:
                    if isinstance(proj, dict):
                        proj_list.append(proj.get('Name', ''))
                projects = '; '.join(proj_list)
            
            # Format recommended roles
            recommended_roles = ''
            if resume.get('Recommended Roles') and isinstance(resume['Recommended Roles'], list):
                recommended_roles = ', '.join(resume['Recommended Roles'])
            
            # Format upload date
            upload_date = ''
            if resume.get('upload_date'):
                if isinstance(resume['upload_date'], str):
                    upload_date = resume['upload_date']
                else:
                    upload_date = resume['upload_date'].strftime('%Y-%m-%d %H:%M:%S')
            
            # Generate full CV URL
            cv_url = ''
            if resume.get('cv_url'):
                cv_url = f"{request.host_url.rstrip('/')}{resume['cv_url']}"
            
            row = [
                resume.get('_id', ''),
                resume.get('Full Name', ''),
                resume.get('Email Address', ''),
                resume.get('Contact Number', ''),
                resume.get('Location', ''),
                resume.get('LinkedIn', ''),
                resume.get('GitHub', ''),
                cv_url,
                tech_skills,
                soft_skills,
                education,
                work_exp,
                projects,
                recommended_roles,
                upload_date,
                resume.get('filename', '')
            ]
            writer.writerow(row)
        
        # Create response
        csv_content = output.getvalue()
        output.close()
        
        response = make_response(csv_content)
        response.headers['Content-Type'] = 'text/csv'
        response.headers['Content-Disposition'] = f'attachment; filename=resumes_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        
        return response
    
    except Exception as e:
        print(f"Error exporting CSV: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Error exporting CSV: {e}'})

@app.route('/view_cv/<cv_id>')
def view_cv(cv_id):
    """Serve CV files via URL"""
    try:
        # Find the resume with this CV ID
        if mongodb_available:
            resume = resumes_collection.find_one({'cv_url': f'/view_cv/{cv_id}'})
        else:
            resume = None
            for r in resumes_data:
                if r.get('cv_url') == f'/view_cv/{cv_id}':
                    resume = r
                    break
        
        if not resume:
            return "CV not found", 404
        
        cv_filename = resume.get('cv_filename')
        if not cv_filename:
            return "CV file not found", 404
        
        cv_file_path = os.path.join(app.config['CV_FOLDER'], cv_filename)
        
        if not os.path.exists(cv_file_path):
            return "CV file not found on disk", 404
        
        return send_from_directory(app.config['CV_FOLDER'], cv_filename)
    
    except Exception as e:
        print(f"Error serving CV: {e}")
        return "Error serving CV", 500

@app.route('/save_application', methods=['POST'])
def save_application():
    application_data = request.json
    application_data['submission_date'] = datetime.now()
    
    try:
        if mongodb_available:
            application_id = applications_collection.insert_one(application_data).inserted_id
            return jsonify({
                'success': True,
                'application_id': str(application_id)
            })
        else:
            application_data['_id'] = str(uuid.uuid4())
            applications_data.append(application_data)
            return jsonify({
                'success': True,
                'application_id': application_data['_id']
            })
    except Exception as e:
        print(f"MongoDB error: {e}")
        application_data['_id'] = str(uuid.uuid4())
        applications_data.append(application_data)
        return jsonify({
            'success': True,
            'application_id': application_data['_id']
        })

if __name__ == '__main__':
    app.run(debug=True)
