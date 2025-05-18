from flask import Flask, request, jsonify, render_template, send_from_directory
import os
import json
from werkzeug.utils import secure_filename
import uuid
from datetime import datetime
import re
import pymongo
from pymongo import MongoClient
import PyPDF2
import io
import pdfplumber
import google.generativeai as genai
import requests

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
ALLOWED_EXTENSIONS = {'pdf', 'docx', 'doc', 'txt', 'rtf', 'csv'}

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload size

# Google Gemini AI Setup
try:
    os.environ["GOOGLE_API_KEY"] = "AIzaSyCxkes43W6HxVyvpKi6PvlEfWxzOv7o630"
    genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
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

# Helper function to extract text from PDF using pdfplumber (from main.py)
def extract_text_from_pdf(file_path):
    try:
        text = ""
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text += page.extract_text() or ""  # Handle None values
        return text
    except Exception as e:
        print(f"Error extracting text with pdfplumber: {e}")
        # Fallback to PyPDF2
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
        # Try with a different encoding if UTF-8 fails
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

# Function to Extract Resume Details using Gemini AI (from main.py)
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
    """
    
    try:
        response = model.generate_content(prompt).text
        
        # Extract JSON from response using regex
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
    r'^\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s*$',  # Proper name format at start of line
    r'name[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})',  # Name: John Doe
    r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s*\n',  # Name at start followed by newline
    r'curriculum\s+vitae\s*(?:of|for)?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})',  # CV of John Doe
    r'resume\s*(?:of|for)?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})',  # Resume of John Doe
    r'([A-Z][A-Z\s]+)',  # ALL CAPS NAME
    r'([A-Z][a-z]+\s+[A-Z][a-z]+)',  # Simple First Last pattern
    r'^([A-Z][a-z]*\.?\s+[A-Z][a-z]+)',  # First initial Last name
    r'^\s*([A-Za-z\.\s]{2,30})\s*$'  # Any name-like text at the beginning (2-30 chars)
]

EMAIL_PATTERNS = [
    r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
    r'email[:\s]+([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,})',
    r'e-mail[:\s]+([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,})',
    r'mail[:\s]+([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,})'
]

PHONE_PATTERNS = [
    r'(?:phone|mobile|cell|contact|tel)[:\s]+(\+?[\d\s\-\.]{7,})',
    r'\b(\+\d{1,3}[\s\-\.]?\d{3}[\s\-\.]?\d{3}[\s\-\.]?\d{4})\b',  # International format
    r'\b(\d{3}[\s\-\.]?\d{3}[\s\-\.]?\d{4})\b',  # US format
    r'\b(\d{4}[\s\-\.]?\d{3}[\s\-\.]?\d{3})\b',  # Some international formats
    r'\b(\d{3,4}[\s\-\.]?\d{6,7})\b'  # Other formats like 0313-7786895
]

LOCATION_PATTERNS = [
    r'(?:location|address|city|residence)[:\s]+([A-Za-z0-9\s,\.\-]+)',
    r'(?:state|province|country)[:\s]+([A-Za-z\s,\.]+)',
    r'(?:zip|postal\s+code)[:\s]+([A-Za-z0-9\s\-]+)'
]

# Enhanced patterns for LinkedIn and GitHub URLs
LINKEDIN_PATTERNS = [
    r'https?://(?:www\.)?linkedin\.com/in/([a-zA-Z0-9_-]+)/?',
    r'linkedin\.com/in/([a-zA-Z0-9_-]+)/?',
    r'(?:linkedin|linked in)[:\s]+(?:https?://)?(?:www\.)?linkedin\.com/in/([a-zA-Z0-9_-]+)/?',
    r'(?:profile|linkedin)[:\s]+(?:https?://)?(?:www\.)?linkedin\.com/in/([a-zA-Z0-9_-]+)/?',
    r'linkedin[:\s]+(?:https?://)?(?:www\.)?linkedin\.com/in/([a-zA-Z0-9_-]+)/?',
    r'linkedin[:\s]+([a-zA-Z0-9_-]+)',  # Just the username after "linkedin:"
    r'linkedin\s*:\s*([^\s,]+)'  # Anything after "linkedin:" that's not a space or comma
]

GITHUB_PATTERNS = [
    r'https?://(?:www\.)?github\.com/([a-zA-Z0-9_-]+)/?',
    r'github\.com/([a-zA-Z0-9_-]+)/?',
    r'(?:github|git hub)[:\s]+(?:https?://)?(?:www\.)?github\.com/([a-zA-Z0-9_-]+)/?',
    r'(?:profile|github)[:\s]+(?:https?://)?(?:www\.)?github\.com/([a-zA-Z0-9_-]+)/?',
    r'github[:\s]+(?:https?://)?(?:www\.)?github\.com/([a-zA-Z0-9_-]+)/?',
    r'github[:\s]+([a-zA-Z0-9_-]+)',  # Just the username after "github:"
    r'github\s*:\s*([^\s,]+)'  # Anything after "github:" that's not a space or comma
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
        
        # Check if this line is a section header
        section_found = False
        for section, patterns in SECTION_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, line, re.IGNORECASE) and len(line) < 50:  # Section headers are usually short
                    current_section = section
                    sections[current_section] = []
                    section_found = True
                    break
            if section_found:
                break
        
        # Add line to current section
        sections[current_section].append(line)
    
    return sections

# Function to extract education details
def extract_education(education_section):
    education = []
    if not education_section:
        return education
    
    # Join the section lines
    text = ' '.join(education_section)
    
    # Look for degree patterns
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
    
    # Extract degrees
    degrees = []
    for pattern in degree_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        degrees.extend(matches)
    
    # Extract institutions
    institutions = []
    for pattern in institution_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        institutions.extend(matches)
    
    # Extract years
    years = []
    for pattern in year_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        years.extend(matches)
    
    # Extract fields
    fields = []
    for pattern in field_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        fields.extend(matches)
    
    # Create education entries
    if degrees or institutions:
        # If we have more degrees than institutions, create an entry for each degree
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
            # Otherwise, create an entry for each institution
            for i in range(len(institutions)):
                edu_entry = {
                    "Institution": institutions[i].strip(),
                    "Degree": degrees[i].strip() if i < len(degrees) else "",
                    "Years": years[i].strip() if i < len(years) else "",
                    "Field": fields[i].strip() if i < len(fields) else ""
                }
                education.append(edu_entry)
    
    # If we couldn't extract structured education, try to extract from individual lines
    if not education and education_section:
        for line in education_section:
            if len(line.strip()) > 10:  # Skip very short lines
                # Check if line contains education-related keywords
                if re.search(r'degree|bachelor|master|phd|university|college|institute|school', line, re.IGNORECASE):
                    degree = ""
                    institution = ""
                    years = ""
                    field = ""
                    
                    # Extract degree
                    for pattern in degree_patterns:
                        degree_match = re.search(pattern, line, re.IGNORECASE)
                        if degree_match:
                            degree = degree_match.group(0).strip()
                            break
                    
                    # Extract institution
                    for pattern in institution_patterns:
                        institution_match = re.search(pattern, line, re.IGNORECASE)
                        if institution_match:
                            institution = institution_match.group(0).strip()
                            break
                    
                    # Extract years
                    for pattern in year_patterns:
                        years_match = re.search(pattern, line, re.IGNORECASE)
                        if years_match:
                            years = years_match.group(0).strip()
                            break
                    
                    # Extract field
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
    
    # Join the section lines
    full_text = ' '.join(experience_section)
    
    # Try to split into individual job entries
    # Look for patterns that might indicate the start of a new job entry
    job_entries = []
    current_entry = []
    
    for line in experience_section:
        # Check if this line might be the start of a new job entry
        if (re.search(r'(?:20\d{2}|19\d{2})\s*-\s*(?:20\d{2}|19\d{2}|present|ongoing)', line, re.IGNORECASE) or
            re.search(r'^[A-Z][a-zA-Z\s&]+,', line) or  # Company name followed by comma
            re.search(r'^[A-Z][a-zA-Z\s&]+\s+\|', line) or  # Company name followed by |
            re.search(r'^(?:senior|junior|lead|principal|software|web|mobile|data|cloud|devops|full\s+stack|front\s*end|back\s*end)\s+', line, re.IGNORECASE)):
            
            # If we have a current entry, add it to job_entries
            if current_entry:
                job_entries.append('\n'.join(current_entry))
            
            # Start a new entry
            current_entry = [line]
        else:
            # Add to current entry
            current_entry.append(line)
    
    # Add the last entry
    if current_entry:
        job_entries.append('\n'.join(current_entry))
    
    # If we couldn't split into job entries, use the whole section as one entry
    if not job_entries:
        job_entries = [full_text]
    
    # Process each job entry
    for entry in job_entries:
        if len(entry.strip()) < 10:  # Skip very short entries
            continue
        
        company = ""
        role = ""
        years = ""
        description = entry.strip()
        
        # Extract company name
        company_patterns = [
            r'(?:at|with|for)?\s*([A-Za-z0-9\s&.,]+?)(?:\n|\s{2,}|$)',
            r'(?:company|employer)[:\s]+([A-Za-z0-9\s&.,]+)',
            r'^([A-Z][a-zA-Z\s&]+),',  # Company name at start of line followed by comma
            r'^([A-Z][a-zA-Z\s&]+)\s+\|'  # Company name at start of line followed by |
        ]
        
        for pattern in company_patterns:
            company_match = re.search(pattern, entry, re.IGNORECASE)
            if company_match:
                company = company_match.group(1).strip()
                break
        
        # Extract role/position
        role_patterns = [
            r'(?:as|position|role|title)[:\s]+([A-Za-z\s]+)',
            r'(?:senior|junior|lead|principal|software|web|mobile|data|cloud|devops|full\s+stack|front\s*end|back\s*end)\s+([A-Za-z\s]+)',
            r'([A-Za-z\s]+?)\s+(?:developer|engineer|analyst|manager|consultant|designer|architect)'
        ]
        
        for pattern in role_patterns:
            role_match = re.search(pattern, entry, re.IGNORECASE)
            if role_match:
                role = role_match.group(1).strip()
                # Add the job title suffix if it's not already included
                if not re.search(r'developer|engineer|analyst|manager|consultant|designer|architect', role, re.IGNORECASE):
                    suffix_match = re.search(r'(developer|engineer|analyst|manager|consultant|designer|architect)', entry, re.IGNORECASE)
                    if suffix_match:
                        role += " " + suffix_match.group(1)
                break
        
        # Extract years
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
        
        # If we have at least a company or role, add the experience
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
    
    # If we have a skills section, prioritize that
    if skills_section:
        skills_text = ' '.join(skills_section)
    else:
        skills_text = text
    
    # Extract technical skills
    for skill in TECHNICAL_SKILLS:
        if re.search(r'\b' + re.escape(skill) + r'\b', skills_text, re.IGNORECASE):
            if skill not in technical_skills:
                technical_skills.append(skill)
    
    # Extract soft skills
    for skill in SOFT_SKILLS:
        if re.search(r'\b' + re.escape(skill) + r'\b', skills_text, re.IGNORECASE):
            if skill not in soft_skills:
                soft_skills.append(skill)
    
    # If we didn't find skills in the skills section, search the entire text
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

# Function to validate and format LinkedIn URL
def validate_linkedin_url(url_or_username):
    # If it's already a full URL
    if url_or_username.startswith('http'):
        # Ensure it's properly formatted
        if '/in/' in url_or_username:
            return url_or_username
        else:
            # Not a valid LinkedIn profile URL
            return ""
    
    # If it's just a username, construct the URL
    username = url_or_username.strip('/')
    # Basic validation - usernames should be 3-100 chars and contain only letters, numbers, hyphens
    if re.match(r'^[a-zA-Z0-9\-]{3,100}$', username):
        return f"https://www.linkedin.com/in/{username}"
    
    # If it contains linkedin.com but not properly formatted
    if 'linkedin.com' in url_or_username:
        match = re.search(r'linkedin\.com/in/([a-zA-Z0-9_-]+)', url_or_username)
        if match:
            return f"https://www.linkedin.com/in/{match.group(1)}"
    
    # Not a valid username
    return ""

# Function to validate and format GitHub URL
def validate_github_url(url_or_username):
    # If it's already a full URL
    if url_or_username.startswith('http'):
        # Ensure it's properly formatted
        if 'github.com/' in url_or_username:
            return url_or_username
        else:
            # Not a valid GitHub URL
            return ""
    
    # If it's just a username, construct the URL
    username = url_or_username.strip('/')
    # Basic validation - GitHub usernames are 1-39 chars, alphanumeric with hyphens
    if re.match(r'^[a-zA-Z0-9\-]{1,39}$', username):
        return f"https://github.com/{username}"
    
    # If it contains github.com but not properly formatted
    if 'github.com' in url_or_username:
        match = re.search(r'github\.com/([a-zA-Z0-9_-]+)', url_or_username)
        if match:
            return f"https://github.com/{match.group(1)}"
    
    # Not a valid username
    return ""

# Function to fetch profile data from LinkedIn and GitHub
def fetch_profile_data(linkedin_url, github_url):
    profile_data = {
        "linkedin": {},
        "github": {}
    }
    
    # Fetch LinkedIn profile data if URL is available
    if linkedin_url:
        try:
            # Note: This is a placeholder. Real implementation would require LinkedIn API access
            # which requires authentication and proper API credentials
            print(f"Would fetch LinkedIn profile data from: {linkedin_url}")
            # In a real implementation, you would use LinkedIn API or web scraping
            # profile_data["linkedin"] = requests.get(f"https://api.linkedin.com/v2/me", headers=headers).json()
        except Exception as e:
            print(f"Error fetching LinkedIn profile: {e}")
    
    # Fetch GitHub profile data if URL is available
    if github_url:
        try:
            # Extract username from GitHub URL
            username = re.search(r'github\.com/([a-zA-Z0-9_-]+)', github_url)
            if username:
                username = username.group(1)
                # GitHub has a public API that doesn't require authentication for basic profile info
                response = requests.get(f"https://api.github.com/users/{username}")
                if response.status_code == 200:
                    profile_data["github"] = response.json()
        except Exception as e:
            print(f"Error fetching GitHub profile: {e}")
    
    return profile_data

# Function to extract personal information
def extract_personal_info(text, personal_section=None):
    personal_info = {
        "Full Name": "",
        "Email Address": "",
        "Contact Number": "",
        "Location": "",
        "LinkedIn": "",
        "GitHub": ""
    }
    
    # If we have a personal section, prioritize that
    if personal_section:
        personal_text = '\n'.join(personal_section)
    else:
        # Use the first 20 lines for personal info if no personal section
        lines = text.split('\n')
        personal_text = '\n'.join(lines[:20])
    
    # Extract name - first try the first line directly if it looks like a name
    first_line = text.split('\n')[0].strip()
    if 5 <= len(first_line) <= 40 and not re.search(r'resume|cv|curriculum|vitae', first_line, re.IGNORECASE):
        words = first_line.split()
        if 1 < len(words) <= 5:  # Most names are 2-5 words
            if all(word[0].isupper() for word in words if len(word) > 1):  # Check if words are capitalized
                personal_info["Full Name"] = first_line
    
    # If name not found from first line, try patterns
    if not personal_info["Full Name"]:
        for pattern in NAME_PATTERNS:
            try:
                matches = re.findall(pattern, personal_text, re.MULTILINE)
                if matches:
                    personal_info["Full Name"] = matches[0].strip()
                    break
            except Exception as e:
                print(f"Error with name pattern '{pattern}': {e}")
                continue
    
    # If name not found in personal section, try the full text
    if not personal_info["Full Name"] and personal_section:
        for pattern in NAME_PATTERNS:
            try:
                matches = re.findall(pattern, text, re.MULTILINE)
                if matches:
                    personal_info["Full Name"] = matches[0].strip()
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
    
    # If email not found in personal section, try the full text
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
    
    # If phone not found in personal section, try the full text
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
    
    # If location not found in personal section, try the full text
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
    
    # Extract LinkedIn URL
    linkedin_found = False
    for pattern in LINKEDIN_PATTERNS:
        try:
            matches = re.findall(pattern, personal_text, re.IGNORECASE)
            if matches:
                # Validate and format the LinkedIn URL
                linkedin_url = validate_linkedin_url(matches[0])
                if linkedin_url:
                    personal_info["LinkedIn"] = linkedin_url
                    linkedin_found = True
                    break
        except Exception as e:
            print(f"Error with LinkedIn pattern '{pattern}': {e}")
            continue
    
    # If LinkedIn not found in personal section, try the full text
    if not linkedin_found:
        for pattern in LINKEDIN_PATTERNS:
            try:
                matches = re.findall(pattern, text, re.IGNORECASE)
                if matches:
                    # Validate and format the LinkedIn URL
                    linkedin_url = validate_linkedin_url(matches[0])
                    if linkedin_url:
                        personal_info["LinkedIn"] = linkedin_url
                        break
            except Exception as e:
                print(f"Error with LinkedIn pattern '{pattern}': {e}")
                continue
    
    # Extract GitHub URL
    github_found = False
    for pattern in GITHUB_PATTERNS:
        try:
            matches = re.findall(pattern, personal_text, re.IGNORECASE)
            if matches:
                # Validate and format the GitHub URL
                github_url = validate_github_url(matches[0])
                if github_url:
                    personal_info["GitHub"] = github_url
                    github_found = True
                    break
        except Exception as e:
            print(f"Error with GitHub pattern '{pattern}': {e}")
            continue
    
    # If GitHub not found in personal section, try the full text
    if not github_found:
        for pattern in GITHUB_PATTERNS:
            try:
                matches = re.findall(pattern, text, re.IGNORECASE)
                if matches:
                    # Validate and format the GitHub URL
                    github_url = validate_github_url(matches[0])
                    if github_url:
                        personal_info["GitHub"] = github_url
                        break
            except Exception as e:
                print(f"Error with GitHub pattern '{pattern}': {e}")
                continue
    
    # If we have LinkedIn or GitHub URLs, fetch additional profile data
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
    
    return list(recommended_roles)[:5]  # Limit to top 5 roles

# Function to score a resume based on a search skill
def score_resume(resume, search_skill):
    score = 0
    search_skill_lower = search_skill.lower()
    
    # 1. Check for skill match (up to 40 points)
    if 'Skills' in resume:
        all_skills = []
        
        # Handle different data structures for Skills
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
            # Handle case where Skills is a string
            all_skills = [resume['Skills']]
        
        # Check for exact match (40 points)
        for skill in all_skills:
            if isinstance(skill, str) and search_skill_lower == skill.lower():
                score += 40
                break
        
        # Check for partial match if no exact match (up to 30 points)
        if score == 0:
            for skill in all_skills:
                if isinstance(skill, str):
                    skill_lower = skill.lower()
                    if search_skill_lower in skill_lower or skill_lower in search_skill_lower:
                        # Longer overlap = higher score
                        overlap_length = min(len(search_skill_lower), len(skill_lower))
                        score += min(30, overlap_length * 2)
                        break
    
    # 2. Check for relevant work experience (up to 30 points)
    if 'Work Experience' in resume and isinstance(resume['Work Experience'], list):
        experience_score = 0
        for job in resume['Work Experience']:
            if not isinstance(job, dict):
                continue
                
            # Check if the skill is mentioned in role or description
            role = job.get('Role', '') if isinstance(job.get('Role', ''), str) else ''
            description = job.get('Description', '') if isinstance(job.get('Description', ''), str) else ''
            company = job.get('Company', '') if isinstance(job.get('Company', ''), str) else ''
            
            role_lower = role.lower()
            description_lower = description.lower()
            
            if search_skill_lower in role_lower or search_skill_lower in description_lower:
                # Calculate years of experience
                years_text = job.get('Years', '') if isinstance(job.get('Years', ''), str) else ''
                years = 0
                
                # Try to extract years from text like "2018-2022" or "3 years"
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
                                    end_year = start_year + 1  # Default if parsing fails
                            
                            years = end_year - start_year
                    except:
                        years = 1  # Default if parsing fails
                elif re.search(r'\d+', years_text):
                    try:
                        years = int(re.search(r'\d+', years_text).group(0))
                    except:
                        years = 1  # Default if parsing fails
                else:
                    years = 1  # Default if no years info
                
                # Score based on years (max 15 points)
                experience_score += min(15, years * 3)
                
                # Additional points if skill is in role title (max 15 points)
                if search_skill_lower in role_lower:
                    experience_score += 15
        
        score += min(30, experience_score)
    
    # 3. Check for relevant projects (up to 15 points)
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
    
    # 4. Check for relevant education (up to 10 points)
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
    
    # 5. Check for certifications (up to 5 points)
    if 'Certifications' in resume and isinstance(resume['Certifications'], list):
        for cert in resume['Certifications']:
            if isinstance(cert, str) and search_skill_lower in cert.lower():
                score += 5
                break
    
    return score

# Improved resume parsing function that combines AI and rule-based approaches
def parse_resume(text):
    # First try to parse with AI if available
    if gemini_available:
        try:
            ai_resume_data = parse_resume_with_ai(text)
            
            # Check if we got a valid response from AI
            if ai_resume_data and not "error" in ai_resume_data:
                # Add upload date
                ai_resume_data["upload_date"] = datetime.now()
                
                # Ensure all expected fields are present
                if "Skills" not in ai_resume_data:
                    ai_resume_data["Skills"] = {"Technical": [], "Soft": []}
                elif not isinstance(ai_resume_data["Skills"], dict):
                    ai_resume_data["Skills"] = {"Technical": ai_resume_data["Skills"] if isinstance(ai_resume_data["Skills"], list) else [], "Soft": []}
                
                if "Education" not in ai_resume_data:
                    ai_resume_data["Education"] = []
                
                if "Work Experience" not in ai_resume_data:
                    ai_resume_data["Work Experience"] = []
                
                if "Recommended Roles" not in ai_resume_data:
                    # Generate recommended roles based on skills
                    ai_resume_data["Recommended Roles"] = generate_recommended_roles(ai_resume_data["Skills"])
                
                # Extract projects if available in the text
                sections = identify_sections(text)
                if 'projects' in sections:
                    projects = []
                    project_lines = sections['projects']
                    current_project = {"Name": "", "Description": ""}
                    
                    for line in project_lines:
                        if re.match(r'^[A-Z]', line) and len(line) < 50:  # Possible project name
                            if current_project["Name"]:  # Save previous project
                                projects.append(current_project)
                                current_project = {"Name": "", "Description": ""}
                            current_project["Name"] = line
                        else:
                            current_project["Description"] += line + " "
                    
                    if current_project["Name"]:  # Add the last project
                        projects.append(current_project)
                    
                    ai_resume_data["Projects"] = projects
                else:
                    ai_resume_data["Projects"] = []
                
                # Validate and format LinkedIn and GitHub URLs
                if "LinkedIn" in ai_resume_data and ai_resume_data["LinkedIn"]:
                    ai_resume_data["LinkedIn"] = validate_linkedin_url(ai_resume_data["LinkedIn"])
                
                if "GitHub" in ai_resume_data and ai_resume_data["GitHub"]:
                    ai_resume_data["GitHub"] = validate_github_url(ai_resume_data["GitHub"])
                
                # Fetch additional profile data if URLs are available
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
            # Fall back to rule-based parsing
    
    # If AI parsing failed or is not available, use rule-based parsing
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
        "upload_date": datetime.now()
    }
    
    # Identify sections in the resume
    sections = identify_sections(text)
    
    # Extract personal information
    personal_info = extract_personal_info(text, sections.get('personal_info', None))
    resume_data.update(personal_info)
    
    # Extract education
    resume_data["Education"] = extract_education(sections.get('education', None))
    
    # Extract work experience
    resume_data["Work Experience"] = extract_experience(sections.get('experience', None))
    
    # Extract skills
    resume_data["Skills"] = extract_skills(text, sections.get('skills', None))
    
    # Extract projects
    if 'projects' in sections:
        projects = []
        project_lines = sections['projects']
        current_project = {"Name": "", "Description": ""}
        
        for line in project_lines:
            if re.match(r'^[A-Z]', line) and len(line) < 50:  # Possible project name
                if current_project["Name"]:  # Save previous project
                    projects.append(current_project)
                    current_project = {"Name": "", "Description": ""}
                current_project["Name"] = line
            else:
                current_project["Description"] += line + " "
        
        if current_project["Name"]:  # Add the last project
            projects.append(current_project)
        
        resume_data["Projects"] = projects
    
    # Generate recommended roles
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

@app.route('/skill_gap')
def skill_gap():
    return render_template('skill_gap.html')

@app.route('/upload_resume', methods=['POST'])
def upload_resume():
    if 'resume' not in request.files:
        return jsonify({'error': 'No file part'})
    
    file = request.files['resume']
    
    if file.filename == '':
        return jsonify({'error': 'No selected file'})
    
    if file and allowed_file(file.filename):
        # Generate a unique filename
        filename = str(uuid.uuid4()) + '_' + secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        # Save the file
        file.save(file_path)
        
        # Extract text from the file
        text = extract_text(file_path)
        
        # Parse the resume
        resume_data = parse_resume(text)
        
        try:
            # Save to MongoDB if available
            if mongodb_available:
                resume_id = resumes_collection.insert_one(resume_data).inserted_id
                resume_data['_id'] = str(resume_id)
            else:
                # Fallback to in-memory storage
                resume_data['_id'] = str(uuid.uuid4())
                resumes_data.append(resume_data)
        except Exception as e:
            print(f"MongoDB error: {e}")
            # Fallback to in-memory storage
            resume_data['_id'] = str(uuid.uuid4())
            resumes_data.append(resume_data)
        
        return jsonify({
            'success': True,
            'resume_id': str(resume_data['_id']),
            'resume_data': resume_data
        })
    
    return jsonify({'error': 'File type not allowed'})

@app.route('/get_resumes', methods=['GET'])
def get_resumes():
    try:
        # Get resumes from MongoDB if available
        if mongodb_available:
            resumes = list(resumes_collection.find().sort('upload_date', -1))
            # Convert ObjectId to string
            for resume in resumes:
                resume['_id'] = str(resume['_id'])
        else:
            # Fallback to in-memory storage
            resumes = resumes_data
    except Exception as e:
        print(f"MongoDB error: {e}")
        # Fallback to in-memory storage
        resumes = resumes_data
    
    return jsonify(resumes)

@app.route('/get_resume/<resume_id>', methods=['GET'])
def get_resume(resume_id):
    try:
        # Get resume from MongoDB if available
        if mongodb_available:
            from bson.objectid import ObjectId
            try:
                resume = resumes_collection.find_one({'_id': ObjectId(resume_id)})
                if resume:
                    resume['_id'] = str(resume['_id'])
                    return jsonify(resume)
            except Exception as e:
                print(f"MongoDB error: {e}")
                # If ObjectId is invalid, try to find in in-memory storage
        
        # Fallback to in-memory storage
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
        # Search resumes in MongoDB if available
        if mongodb_available:
            resumes = list(resumes_collection.find())
            # Convert ObjectId to string
            for resume in resumes:
                resume['_id'] = str(resume['_id'])
        else:
            # Fallback to in-memory storage
            resumes = resumes_data
        
        # Filter and score resumes based on the search skill
        scored_resumes = []
        for resume in resumes:
            # Check if resume has the skill
            has_skill = False
            if 'Skills' in resume:
                all_skills = []
                
                # Handle different data structures for Skills
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
                    # Handle case where Skills is a string
                    all_skills = [resume['Skills']]
                
                # Check for exact or partial match
                skill_lower = skill.lower()
                for resume_skill in all_skills:
                    if isinstance(resume_skill, str):  # Ensure resume_skill is a string
                        resume_skill_lower = resume_skill.lower()
                        if skill_lower == resume_skill_lower or skill_lower in resume_skill_lower or resume_skill_lower in skill_lower:
                            has_skill = True
                            break
            
            if has_skill:
                # Score the resume
                score = score_resume(resume, skill)
                
                # Add score and rank to resume
                resume_copy = resume.copy()
                resume_copy['score'] = score
                scored_resumes.append(resume_copy)
        
        # Sort resumes by score (highest first)
        scored_resumes.sort(key=lambda x: x['score'], reverse=True)
        
        # Add rank to each resume
        for i, resume in enumerate(scored_resumes):
            resume['rank'] = i + 1
            # Add a visible rank label for frontend display
            resume['rank_label'] = f"#{i+1}"
        
        return jsonify(scored_resumes)
    
    except Exception as e:
        print(f"Error searching resumes: {e}")
        import traceback
        traceback.print_exc()  # Print the full stack trace for debugging
        return jsonify({'error': f'Error searching resumes: {e}'})

@app.route('/save_application', methods=['POST'])
def save_application():
    application_data = request.json
    application_data['submission_date'] = datetime.now()
    
    try:
        # Save to MongoDB if available
        if mongodb_available:
            application_id = applications_collection.insert_one(application_data).inserted_id
            return jsonify({
                'success': True,
                'application_id': str(application_id)
            })
        else:
            # Fallback to in-memory storage
            application_data['_id'] = str(uuid.uuid4())
            applications_data.append(application_data)
            return jsonify({
                'success': True,
                'application_id': application_data['_id']
            })
    except Exception as e:
        print(f"MongoDB error: {e}")
        # Fallback to in-memory storage
        application_data['_id'] = str(uuid.uuid4())
        applications_data.append(application_data)
        return jsonify({
            'success': True,
            'application_id': application_data['_id']
        })

@app.route('/skill_gap_analysis', methods=['POST'])
def skill_gap_analysis():
    data = request.json
    resume_id = data.get('resume_id')
    job_skills = data.get('job_skills', [])
    
    if not resume_id:
        return jsonify({'error': 'Resume ID is required'})
    
    if not job_skills:
        return jsonify({'error': 'Job skills are required'})
    
    try:
        # Get resume from MongoDB if available
        resume = None
        if mongodb_available:
            from bson.objectid import ObjectId
            try:
                resume = resumes_collection.find_one({'_id': ObjectId(resume_id)})
                if resume:
                    resume['_id'] = str(resume['_id'])
            except Exception as e:
                print(f"MongoDB error: {e}")
                # If ObjectId is invalid, try to find in in-memory storage
        
        # Fallback to in-memory storage if not found in MongoDB
        if not resume:
            for r in resumes_data:
                if r['_id'] == resume_id:
                    resume = r
                    break
        
        if not resume:
            return jsonify({'error': 'Resume not found'})
    except Exception as e:
        print(f"Error retrieving resume: {e}")
        return jsonify({'error': f'Error retrieving resume: {e}'})
    
    # Get candidate skills - handle different possible data structures
    resume_skills = []
    if 'Skills' in resume:
        if isinstance(resume['Skills'], dict) and 'Technical' in resume['Skills']:
            resume_skills = resume['Skills']['Technical']
        elif isinstance(resume['Skills'], list):
            resume_skills = resume['Skills']
    
    # Normalize skills for better matching (lowercase)
    normalized_resume_skills = [skill.lower() for skill in resume_skills]
    normalized_job_skills = [skill.lower() for skill in job_skills]
    
    # Calculate matching and missing skills with improved matching logic
    matching_skills = []
    missing_skills = []
    
    for job_skill in job_skills:
        job_skill_lower = job_skill.lower()
        skill_found = False
        
        # Check for exact matches first
        if job_skill_lower in normalized_resume_skills:
            matching_skills.append(job_skill)
            skill_found = True
            continue
            
        # Then check for partial matches
        for i, resume_skill_lower in enumerate(normalized_resume_skills):
            # Check if job skill is part of resume skill or vice versa
            if (job_skill_lower in resume_skill_lower or 
                resume_skill_lower in job_skill_lower or
                # Check for similar skills with slight variations
                (len(job_skill_lower) > 3 and 
                 len(resume_skill_lower) > 3 and
                 (job_skill_lower[:4] == resume_skill_lower[:4] or
                  job_skill_lower[-4:] == resume_skill_lower[-4:]))):
                matching_skills.append(job_skill)
                skill_found = True
                break
        
        if not skill_found:
            missing_skills.append(job_skill)
    
    # Calculate match percentage
    match_percentage = 0
    if job_skills:
        match_percentage = round((len(matching_skills) / len(job_skills)) * 100)
    
    # Generate course recommendations
    course_recommendations = {}
    skill_courses = {
        'Python': 'https://www.udemy.com/course/complete-python-bootcamp/',
        'JavaScript': 'https://www.udemy.com/course/the-complete-javascript-course/',
        'React': 'https://www.udemy.com/course/react-the-complete-guide-incl-redux/',
        'Angular': 'https://www.udemy.com/course/the-complete-guide-to-angular-2/',
        'Vue.js': 'https://www.udemy.com/course/vuejs-2-the-complete-guide/',
        'Node.js': 'https://www.udemy.com/course/nodejs-the-complete-guide/',
        'SQL': 'https://www.udemy.com/course/the-complete-sql-bootcamp/',
        'MongoDB': 'https://www.udemy.com/course/mongodb-the-complete-developers-guide/',
        'NoSQL': 'https://www.udemy.com/course/mongodb-the-complete-developers-guide/',
        'AWS': 'https://www.udemy.com/course/aws-certified-solutions-architect-associate/',
        'Docker': 'https://www.udemy.com/course/docker-and-kubernetes-the-complete-guide/',
        'Kubernetes': 'https://www.udemy.com/course/kubernetes-microservices/',
        'Machine Learning': 'https://www.coursera.org/learn/machine-learning',
        'Deep Learning': 'https://www.coursera.org/specializations/deep-learning',
        'Blockchain': 'https://www.udemy.com/course/blockchain-developer/',
        'Solidity': 'https://www.udemy.com/course/ethereum-and-solidity-the-complete-developers-guide/',
        'Web3': 'https://www.udemy.com/course/web3-blockchain-developer/',
        'API Design': 'https://www.udemy.com/course/nodejs-api-masterclass/',
        'Authentication': 'https://www.udemy.com/course/nodejs-the-complete-guide/',
        'Security': 'https://www.udemy.com/course/web-security-essentials/',
        'Microservices': 'https://www.udemy.com/course/microservices-with-node-js-and-react/',
        'Cloud Services': 'https://www.udemy.com/course/aws-certified-solutions-architect-associate/',
        'Git': 'https://www.udemy.com/course/git-complete/',
        'Testing': 'https://www.udemy.com/course/javascript-unit-testing-the-practical-guide/',
        'CI/CD': 'https://www.udemy.com/course/devops-with-docker-kubernetes-and-azure-devops/',
        'Java': 'https://www.udemy.com/course/java-the-complete-java-developer-course/',
        'HTML': 'https://www.udemy.com/course/design-and-develop-a-killer-website-with-html5-and-css3/',
        'CSS': 'https://www.udemy.com/course/advanced-css-and-sass/',
        'TypeScript': 'https://www.udemy.com/course/understanding-typescript/',
        'PHP': 'https://www.udemy.com/course/php-for-complete-beginners-includes-msql-object-oriented/',
        'Ruby': 'https://www.udemy.com/course/learn-to-code-with-ruby-lang/',
        'Swift': 'https://www.udemy.com/course/ios-13-app-development-bootcamp/',
        'Kotlin': 'https://www.udemy.com/course/kotlin-android-developer-masterclass/',
        'Go': 'https://www.udemy.com/course/go-the-complete-developers-guide/',
        'Rust': 'https://www.udemy.com/course/rust-lang/',
        'C#': 'https://www.udemy.com/course/complete-csharp-masterclass/',
        'C++': 'https://www.udemy.com/course/beginning-c-plus-plus-programming/',
        'UI/UX': 'https://www.udemy.com/course/ui-ux-web-design-using-adobe-xd/',
        'Responsive Design': 'https://www.udemy.com/course/responsive-web-design-tutorial-course-html5-and-css3-bootstrap/'
    }
    
    for skill in missing_skills:
        # First try exact match
        if skill in skill_courses:
            course_recommendations[skill] = skill_courses[skill]
        else:
            # Try partial match
            skill_lower = skill.lower()
            for key, url in skill_courses.items():
                key_lower = key.lower()
                if key_lower in skill_lower or skill_lower in key_lower:
                    course_recommendations[skill] = url
                    break
    
    return jsonify({
        'resume_skills': resume_skills,
        'job_skills': job_skills,
        'matching_skills': matching_skills,
        'missing_skills': missing_skills,
        'match_percentage': match_percentage,
        'course_recommendations': course_recommendations
    })

# Now let's fix the get_skill_gap route
@app.route('/get_skill_gap/<resume_id>/<job_role>', methods=['GET'])
def get_skill_gap(resume_id, job_role):
    # Define required skills for common job roles
    job_skills = {
        'Frontend Developer': [
            'HTML', 'CSS', 'JavaScript', 'React', 'Angular', 'Vue.js', 'Responsive Design', 
            'UI/UX', 'Git', 'Testing', 'Performance Optimization'
        ],
        'Backend Developer': [
            'Python', 'Java', 'Node.js', 'SQL', 'NoSQL', 'API Design', 'Authentication', 
            'Security', 'Docker', 'Microservices', 'Cloud Services'
        ],
        'Full Stack Developer': [
            'HTML', 'CSS', 'JavaScript', 'React', 'Angular', 'Vue.js', 'Python', 'Java', 'Node.js', 
            'SQL', 'NoSQL', 'API Design', 'Git', 'Docker', 'Cloud Services'
        ],
        'Data Scientist': [
            'Python', 'R', 'SQL', 'Machine Learning', 'Deep Learning', 'Data Visualization', 
            'Statistical Analysis', 'Pandas', 'NumPy', 'TensorFlow', 'PyTorch'
        ],
        'DevOps Engineer': [
            'Linux', 'Scripting', 'CI/CD', 'Docker', 'Kubernetes', 'Cloud Services', 
            'Monitoring', 'Security', 'Networking', 'Infrastructure as Code'
        ],
        'Mobile Developer': [
            'Swift', 'Kotlin', 'Java', 'React Native', 'Flutter', 'Mobile UI Design', 
            'API Integration', 'Performance Optimization', 'App Store Deployment'
        ],
        'UI/UX Designer': [
            'Figma', 'Sketch', 'Adobe XD', 'User Research', 'Wireframing', 'Prototyping', 
            'Visual Design', 'Interaction Design', 'Usability Testing'
        ],
        'Blockchain Developer': [
            'Solidity', 'Ethereum', 'Smart Contracts', 'Web3.js', 'Truffle', 'Hardhat',
            'Blockchain Architecture', 'Cryptography', 'DApps', 'JavaScript', 'Security'
        ]
    }
    
    try:
        # Get resume from MongoDB if available
        resume = None
        if mongodb_available:
            from bson.objectid import ObjectId
            try:
                resume = resumes_collection.find_one({'_id': ObjectId(resume_id)})
                if resume:
                    resume['_id'] = str(resume['_id'])
            except Exception as e:
                print(f"MongoDB error: {e}")
                # If ObjectId is invalid, try to find in in-memory storage
        
        # Fallback to in-memory storage if not found in MongoDB
        if not resume:
            for r in resumes_data:
                if r['_id'] == resume_id:
                    resume = r
                    break
        
        if not resume:
            return jsonify({'error': 'Resume not found'})
    except Exception as e:
        print(f"Error retrieving resume: {e}")
        return jsonify({'error': f'Error retrieving resume: {e}'})
    
    # Get candidate skills - handle different possible data structures
    candidate_skills = []
    if 'Skills' in resume:
        if isinstance(resume['Skills'], dict) and 'Technical' in resume['Skills']:
            candidate_skills = resume['Skills']['Technical']
        elif isinstance(resume['Skills'], list):
            candidate_skills = resume['Skills']
    
    # Normalize skills for better matching (lowercase)
    normalized_candidate_skills = [skill.lower() for skill in candidate_skills]
    
    # Get required skills for the job role
    required_skills = []
    if job_role in job_skills:
        required_skills = job_skills[job_role]
    else:
        # If job role not found, use a generic set of skills
        required_skills = job_skills['Full Stack Developer']
    
    normalized_required_skills = [skill.lower() for skill in required_skills]
    
    # Calculate matching and missing skills with improved matching logic
    matching_skills = []
    missing_skills = []
    
    for req_skill in required_skills:
        req_skill_lower = req_skill.lower()
        skill_found = False
        
        # Check for exact matches first
        if req_skill_lower in normalized_candidate_skills:
            matching_skills.append(req_skill)
            skill_found = True
            continue
            
        # Then check for partial matches
        for cand_skill_lower in normalized_candidate_skills:
            # Check if required skill is part of candidate skill or vice versa
            if (req_skill_lower in cand_skill_lower or 
                cand_skill_lower in req_skill_lower or
                # Check for similar skills with slight variations
                (len(req_skill_lower) > 3 and 
                 len(cand_skill_lower) > 3 and
                 (req_skill_lower[:4] == cand_skill_lower[:4] or
                  req_skill_lower[-4:] == cand_skill_lower[-4:]))):
                matching_skills.append(req_skill)
                skill_found = True
                break
        
        if not skill_found:
            missing_skills.append(req_skill)
    
    # Calculate match percentage
    match_percentage = 0
    if required_skills:
        match_percentage = round((len(matching_skills) / len(required_skills)) * 100)
    
    # Generate recommendations
    recommendations = []
    if missing_skills:
        recommendations.append(f"Consider learning {', '.join(missing_skills[:3])} to improve your qualifications for this role.")
    
    if match_percentage < 50:
        recommendations.append("Your skill set might be better suited for a different role or consider upskilling.")
    elif match_percentage < 70:
        recommendations.append("You have a good foundation but would benefit from additional training.")
    else:
        recommendations.append("You have a strong match for this role!")
    
    # Add specific course recommendations
    skill_courses = {
        'Python': {'title': 'Complete Python Bootcamp', 'url': 'https://www.udemy.com/course/complete-python-bootcamp/'},
        'JavaScript': {'title': 'Modern JavaScript', 'url': 'https://www.udemy.com/course/the-complete-javascript-course/'},
        'React': {'title': 'React - The Complete Guide', 'url': 'https://www.udemy.com/course/react-the-complete-guide-incl-redux/'},
        'Angular': {'title': 'Angular - The Complete Guide', 'url': 'https://www.udemy.com/course/the-complete-guide-to-angular-2/'},
        'Vue.js': {'title': 'Vue.js - The Complete Guide', 'url': 'https://www.udemy.com/course/vuejs-2-the-complete-guide/'},
        'Node.js': {'title': 'Node.js - The Complete Guide', 'url': 'https://www.udemy.com/course/nodejs-the-complete-guide/'},
        'SQL': {'title': 'The Complete SQL Bootcamp', 'url': 'https://www.udemy.com/course/the-complete-sql-bootcamp/'},
        'NoSQL': {'title': 'MongoDB - The Complete Guide', 'url': 'https://www.udemy.com/course/mongodb-the-complete-developers-guide/'},
        'Machine Learning': {'title': 'Machine Learning A-Z', 'url': 'https://www.udemy.com/course/machinelearning/'},
        'Docker': {'title': 'Docker & Kubernetes', 'url': 'https://www.udemy.com/course/docker-and-kubernetes-the-complete-guide/'},
        'Cloud Services': {'title': 'AWS Certified Solutions Architect', 'url': 'https://www.udemy.com/course/aws-certified-solutions-architect-associate/'},
        'Solidity': {'title': 'Ethereum and Solidity', 'url': 'https://www.udemy.com/course/ethereum-and-solidity-the-complete-developers-guide/'},
        'Blockchain': {'title': 'Blockchain A-Z', 'url': 'https://www.udemy.com/course/blockchain-developer/'},
        'Java': {'title': 'Java Programming Masterclass', 'url': 'https://www.udemy.com/course/java-the-complete-java-developer-course/'},
        'API Design': {'title': 'Node.js API Masterclass', 'url': 'https://www.udemy.com/course/nodejs-api-masterclass/'},
        'Authentication': {'title': 'Web Security & Authentication', 'url': 'https://www.udemy.com/course/web-security-authentication/'},
        'Security': {'title': 'Web Security Essentials', 'url': 'https://www.udemy.com/course/web-security-essentials/'},
        'Microservices': {'title': 'Microservices with Node.js', 'url': 'https://www.udemy.com/course/microservices-with-node-js-and-react/'},
        'Git': {'title': 'Git Complete', 'url': 'https://www.udemy.com/course/git-complete/'},
        'Testing': {'title': 'JavaScript Unit Testing', 'url': 'https://www.udemy.com/course/javascript-unit-testing-the-practical-guide/'},
        'CI/CD': {'title': 'DevOps CI/CD Pipeline', 'url': 'https://www.udemy.com/course/devops-with-docker-kubernetes-and-azure-devops/'},
        'HTML': {'title': 'HTML5 and CSS3', 'url': 'https://www.udemy.com/course/design-and-develop-a-killer-website-with-html5-and-css3/'},
        'CSS': {'title': 'Advanced CSS and Sass', 'url': 'https://www.udemy.com/course/advanced-css-and-sass/'},
        'Responsive Design': {'title': 'Responsive Web Design', 'url': 'https://www.udemy.com/course/responsive-web-design-tutorial-course-html5-css3-bootstrap/'},
        'UI/UX': {'title': 'UI/UX Design with Figma', 'url': 'https://www.udemy.com/course/ui-ux-web-design-using-adobe-xd/'},
        'Performance Optimization': {'title': 'Web Performance Optimization', 'url': 'https://www.udemy.com/course/website-performance/'},
        'Linux': {'title': 'Linux Administration', 'url': 'https://www.udemy.com/course/linux-administration-bootcamp/'},
        'Kubernetes': {'title': 'Kubernetes for Beginners', 'url': 'https://www.udemy.com/course/learn-kubernetes/'},
        'Networking': {'title': 'Computer Networking', 'url': 'https://www.udemy.com/course/introduction-to-computer-networks/'},
        'Infrastructure as Code': {'title': 'Terraform Course', 'url': 'https://www.udemy.com/course/terraform-beginner-to-advanced/'},
        'Swift': {'title': 'iOS Development', 'url': 'https://www.udemy.com/course/ios-13-app-development-bootcamp/'},
        'Kotlin': {'title': 'Android with Kotlin', 'url': 'https://www.udemy.com/course/kotlin-android-developer-masterclass/'},
        'Flutter': {'title': 'Flutter Development', 'url': 'https://www.udemy.com/course/flutter-bootcamp-with-dart/'},
        'React Native': {'title': 'React Native', 'url': 'https://www.udemy.com/course/react-native-the-practical-guide/'},
        'Figma': {'title': 'Figma UI UX Design', 'url': 'https://www.udemy.com/course/figma-ux-ui-design-user-experience-tutorial/'},
        'Sketch': {'title': 'Sketch App Design', 'url': 'https://www.udemy.com/course/sketch-design/'},
        'Adobe XD': {'title': 'Adobe XD Design', 'url': 'https://www.udemy.com/course/adobe-xd-experience-design-ux-ui-design/'},
        'User Research': {'title': 'UX Research', 'url': 'https://www.udemy.com/course/ux-research-methods/'},
        'Wireframing': {'title': 'Wireframing with Figma', 'url': 'https://www.udemy.com/course/wireframing-with-figma/'},
        'Prototyping': {'title': 'Prototyping in Figma', 'url': 'https://www.udemy.com/course/prototyping-in-figma/'},
        'Visual Design': {'title': 'Visual Design', 'url': 'https://www.udemy.com/course/visual-design-for-ui-ux-designers/'},
        'Interaction Design': {'title': 'Interaction Design', 'url': 'https://www.udemy.com/course/interaction-design-for-usability/'},
        'Usability Testing': {'title': 'Usability Testing', 'url': 'https://www.udemy.com/course/usability-testing-for-ux-designers/'},
        'Web3.js': {'title': 'Web3.js Ethereum', 'url': 'https://www.udemy.com/course/ethereum-and-solidity-the-complete-developers-guide/'},
        'Truffle': {'title': 'Truffle Framework', 'url': 'https://www.udemy.com/course/ethereum-dapp/'},
        'Hardhat': {'title': 'Hardhat Ethereum', 'url': 'https://www.udemy.com/course/hardhat-tutorial-ethereum/'},
        'Cryptography': {'title': 'Cryptography Basics', 'url': 'https://www.udemy.com/course/cryptography-for-beginners/'},
        'DApps': {'title': 'Decentralized Applications', 'url': 'https://www.udemy.com/course/ethereum-dapp/'}
    }
    
    # Create course recommendations object
    course_recommendations = {}
    for skill in missing_skills:
        # First try exact match
        if skill in skill_courses:
            course_recommendations[skill] = skill_courses[skill]['url']
        else:
            # Try partial match
            skill_lower = skill.lower()
            for key, value in skill_courses.items():
                key_lower = key.lower()
                if key_lower in skill_lower or skill_lower in key_lower:
                    course_recommendations[skill] = value['url']
                    break
    
    return jsonify({
        'job_role': job_role,
        'candidate_skills': candidate_skills,
        'required_skills': required_skills,
        'match_percentage': match_percentage,
        'matching_skills': matching_skills,
        'missing_skills': missing_skills,
        'recommendations': recommendations,
        'course_recommendations': course_recommendations
    })

if __name__ == '__main__':
    app.run(debug=True)