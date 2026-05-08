function obj = errorbar2(x,y)

disp(x);
[y_mean,y_sd] = mean2(y);
obj = errorbar(x,y_mean,y_sd);

end