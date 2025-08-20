"""FastAPI Tasks API

CRUD для задач с использованием FastAPI + SQLModel (SQLite).
Соответствует PEP8, включает типизацию, валидацию и аккуратную структуру кода
в одном файле (удобно для тестового задания).

Функционал:
- POST   /tasks            — создать задачу
- GET    /tasks/{task_id}  — получить одну задачу по UUID
- GET    /tasks            — получить список задач (фильтр/поиск/пагинация)
- PATCH  /tasks/{task_id}  — частично обновить задачу
- DELETE /tasks/{task_id}  — удалить задачу

Дополнительно:
- Фильтрация по статусу (?status=created|in_progress|completed)
- Поиск по подстроке в title/description (?q=...)
- Пагинация (?limit=20&offset=0)
- Поля created_at/updated_at
- Заголовок Location при создании

Как запустить:
    uvicorn main:app --reload

Зависимости (пример requirements.txt):
    fastapi>=0.112
    uvicorn[standard]>=0.30
    sqlmodel>=0.0.21

"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Optional
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlmodel import Field as SQLField, Session, SQLModel, create_engine, select
from sqlmodel.sql.expression import Select, col




class StatusEnum(str, Enum):
    created = "created"
    in_progress = "in_progress"
    completed = "completed"


class Task(SQLModel, table=True):
    __tablename__ = "tasks"

    id: UUID = SQLField(default_factory=uuid4, primary_key=True, index=True)
    title: str = SQLField(min_length=1, max_length=200, index=True)
    description: Optional[str] = SQLField(default=None, max_length=2000)
    status: StatusEnum = SQLField(default=StatusEnum.created, index=True)

    created_at: datetime = SQLField(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = SQLField(default_factory=datetime.utcnow, nullable=False)


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    status: Optional[StatusEnum] = None


class TaskRead(BaseModel):
    id: UUID
    title: str
    description: Optional[str]
    status: StatusEnum
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    status: Optional[StatusEnum] = None




DATABASE_URL = "sqlite:///./taskss.db"
engine = create_engine(DATABASE_URL, echo=False)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)



def get_session() -> Session:
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]



app = FastAPI(title="Tasks API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()




def apply_filters(
    stmt: Select, *, status_: Optional[StatusEnum], q: Optional[str]
) -> Select:
    if status_ is not None:
        stmt = stmt.where(Task.status == status_)

    if q:
        # Кейс-инсенситив поиск по title/description
        pattern = f"%{q.lower()}%"
        stmt = stmt.where(
            (col(Task.title).lower().like(pattern))
            | (col(Task.description).lower().like(pattern))
        )
    return stmt



@app.post(
    "/tasks",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать задачу",
)
def create_task(payload: TaskCreate, response: Response, session: SessionDep) -> TaskRead:
    task = Task(
        title=payload.title,
        description=payload.description,
        status=payload.status or StatusEnum.created,
    )
    session.add(task)
    session.commit()
    session.refresh(task)

    response.headers["Location"] = f"/tasks/{task.id}"
    return TaskRead.from_orm(task)


@app.get("/tasks/{task_id}", response_model=TaskRead, summary="Получить задачу по UUID")
def get_task(task_id: UUID, session: SessionDep) -> TaskRead:
    task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskRead.from_orm(task)


@app.get(
    "/tasks",
    response_model=list[TaskRead],
    summary="Список задач с фильтрами и пагинацией",
)
def list_tasks(
    session: SessionDep,
    status_: Optional[StatusEnum] = Query(None, alias="status", description="Фильтр по статусу"),
    q: Optional[str] = Query(None, min_length=1, description="Поиск по подстроке"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[TaskRead]:
    stmt = select(Task).order_by(Task.created_at.desc())
    stmt = apply_filters(stmt, status_=status_, q=q)
    stmt = stmt.limit(limit).offset(offset)

    tasks = session.exec(stmt).all()
    return [TaskRead.from_orm(t) for t in tasks]


@app.patch(
    "/tasks/{task_id}",
    response_model=TaskRead,
    summary="Частичное обновление задачи",
)
def update_task(task_id: UUID, payload: TaskUpdate, session: SessionDep) -> TaskRead:
    task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    updated = False

    if payload.title is not None and payload.title != task.title:
        task.title = payload.title
        updated = True

    if payload.description is not None and payload.description != task.description:
        task.description = payload.description
        updated = True

    if payload.status is not None and payload.status != task.status:
        task.status = payload.status
        updated = True

    if updated:
        task.updated_at = datetime.utcnow()
        session.add(task)
        session.commit()
        session.refresh(task)

    return TaskRead.from_orm(task)


@app.delete(
    "/tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить задачу",
)
def delete_task(task_id: UUID, session: SessionDep) -> Response:
    task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    session.delete(task)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


