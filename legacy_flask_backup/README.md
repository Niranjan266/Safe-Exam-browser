

---

# 🛡️ Safe Exam Browser  

**Safe Exam Browser (SEB)** is a secure web application designed to provide a controlled environment for online examinations. It restricts access to system resources, external websites, and unauthorized applications during an exam session, ensuring academic integrity and preventing cheating.  

---

## 🚀 Features  
- 🔒 **Secure Exam Environment** – Locks down the system during exams  
- 🌐 **Restricted Browsing** – Only allows access to whitelisted exam portals  
- 🖥️ **Full-Screen Mode** – Prevents switching between applications  
- 🛑 **Block Shortcuts** – Disables copy-paste, screen capture, and other shortcuts  
- 👤 **Authentication** – Supports student login and exam session validation  
- ⚙️ **Configurable Settings** – Admins can customize restrictions and permissions  

---

## 🛠️ Technologies Used  
- **Python (Flask)** – Backend framework  
- **HTML5, CSS3, JavaScript** – Frontend structure and styling  
- **SQLite** – Database for storing exam and user data  
- **Jinja2 Templates** – Dynamic rendering of exam pages  
- **VS Code / GitHub** – Development and version control  

---

## 📂 Project Structure  
```bash
Safe-Exam-browser/
│── app.py               # Main Flask application entry point
│── config.py            # Configuration settings (DB URI, environment variables)
│── init_db.py           # Script to initialize database schema
│── seed_admin.py        # Script to insert default admin credentials
│── requirements.txt     # Python dependencies
│── README.md            # Documentation
│
├── static/              # Static assets (CSS, JS, images)
│   ├── css/
│   │   └── style.css    # Main stylesheet
│   ├── js/
│   │   └── script.js    # Client-side logic (form validation, voice alerts, etc.)
│   └── images/
│       ├── logo.png
│       └── screenshots/ # Screenshots for README
│           ├── login.png
│           ├── exam.png
│           └── dashboard.png
│
├── templates/           # Jinja2 HTML templates
│   ├── base.html        # Base layout template
│   ├── login.html       # Admin/Student login page
│   ├── exam.html        # Exam interface (locked environment)
│   ├── dashboard.html   # Admin dashboard for managing exams/users
│   └── settings.html    # Configurable restrictions page
│
├── instance/            # Database instance files
│   └── exam.db          # SQLite database storing users, exams, sessions
│
├── __pycache__/         # Compiled Python cache files
└── .vscode/             # VS Code workspace settings
```

---

## 📖 Getting Started  

### 1. Clone the Repository  
```bash
git clone https://github.com/Niranjan266/Safe-Exam-browser.git
cd Safe-Exam-browser
```

### 2. Install Dependencies  
```bash
pip install -r requirements.txt
```

### 3. Initialize the Database  
```bash
python init_db.py
```

### 4. Run the Application  
```bash
python app.py
```
Open your browser and navigate to **`http://127.0.0.1:5000/`**  

---

## 📸 Screenshots  

- **Login Page** – Secure student authentication  
  `https://github.com/user-attachments/assets/bc14cd57-f1d3-4d6e-a1d5-575d7fddf82a`  

- **Exam Page** – Full-screen exam environment with restrictions  
  `https://github.com/user-attachments/assets/ce2890b6-2037-44ed-ba94-8c6c17806f58`  

- **Dashboard** – Admin view for managing exams and users  
  `https://github.com/user-attachments/assets/5b395f35-40d3-4922-a06c-170c1c898328`  

- **Live Student Status Page** – Monitoring the student live status  
  `https://github.com/user-attachments/assets/be89324c-a163-4664-8dfd-a527d5d5cf35`  

---

## 📌 Future Enhancements  
- 🔐 Integration with biometric authentication  
- 📊 Analytics dashboard for exam monitoring  
- ☁️ Cloud deployment for scalability  
- 🎙️ AI-powered proctoring with voice/video monitoring  

---

## 👨‍💻 Author  
Developed by **Niranjan266**  
GitHub Profile: Niranjan266 [(github.com)](https://www.bing.com/search?q="https%3A%2F%2Fgithub.com%2FNiranjan266")  

---
