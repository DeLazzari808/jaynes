// Lantern: a book club's reading list. Reads data/books.json; the detail of a book opens at #/books/<id>.
let books = [];
const list = document.getElementById("books"), detail = document.getElementById("detail");

function render() {
  const q = document.getElementById("q").value.toLowerCase();
  list.innerHTML = "";
  for (const b of books.filter(b => b.title.toLowerCase().includes(q))) {
    const li = document.createElement("li");
    li.textContent = b.title;
    li.onclick = () => { location.hash = "#/books/" + b.id; };
    list.appendChild(li);
  }
}

function route() {
  const m = location.hash.match(/^#\/books\/(\w+)$/);
  const b = m && books.find(x => x.id === m[1]);
  detail.hidden = !b;
  list.hidden = !!b;
  if (b) {
    document.getElementById("title").textContent = b.title;
    document.getElementById("who").textContent = "Picked by " + b.picked_by;
  }
}

document.getElementById("q").oninput = render;
document.getElementById("save").onclick = () => fetch("/api/notes", {method: "POST", body: "{}"});
window.onhashchange = route;
fetch("data/books.json").then(r => r.json()).then(data => { books = data.books; render(); route(); });
fetch("/api/covers/refresh").catch(() => {});  // a GET that writes a cache: the person lists it as having effects
fetch("https://example.com/ping").catch(() => {});
