"use strict";

// Shared Mocha setup: `should` extends Object.prototype as a side effect, and
// every spec exercises the same App instance through `appTester` and `authData`.
require("should");
const zapier = require("zapier-platform-core");

const App = require("../index");

const appTester = zapier.createAppTester(App);
const authData = { apiKey: "hsk_test", apiUrl: "https://api.example.com" };

module.exports = { App, appTester, authData };
