export class App {
    constructor(items) {
        this.items = items;
    }

    start() {
        return computeTotal(this.items);
    }
}

export function computeTotal(items) {
    return items.reduce((total, item) => total + item.price, 0);
}
