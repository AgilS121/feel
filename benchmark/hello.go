package main

import (
	"fmt"
	"net/http"
)

func main() {
	http.HandleFunc("/hello", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `"Hello, World"`)
	})
	fmt.Println("Go server on :3003")
	http.ListenAndServe(":3003", nil)
}
