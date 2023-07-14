import aiodocker
from typing import Annotated, BinaryIO
from fastapi import BackgroundTasks, FastAPI, Request, File, Form, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

docker = aiodocker.Docker()

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")


@app.on_event("startup")
async def startup():
    info = await docker.system.info()

    if info["Swarm"]["LocalNodeState"] == "inactive":
        await docker.swarm.init()


@app.get("/deploy", response_class=HTMLResponse)
def deploy_page(request: Request):
    return templates.TemplateResponse("deploy.html.j2", {"request": request})


async def deploy_service(name: str, file: BinaryIO):
    stream = docker.images.build(
        fileobj=file.file,
        tag=name,
        stream=True,
        encoding="utf-8"
    )

    async for line in stream:
        print(line)

    await docker.services.create(
        task_template={
            "ContainerSpec": {
                "Image": name
            }
        },
        name=name
    )


@app.post("/deploy", response_class=RedirectResponse)
async def deploy(
    name: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    background_tasks: BackgroundTasks
):
    background_tasks.add_task(deploy_service, name, file)

    return "/"


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
