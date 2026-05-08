function [a,b,r2] = getExpReg(x_data,y_data)
len = length(x_data);
x = zeros(len,1);
y = zeros(len,1);
x(:) = x_data(:);
y(:) = y_data(:);

[f,gof] = fit(x,y,'exp1');
coeffs = coeffvalues(f);
a = coeffs(1);
b = coeffs(2);
r2 = gof.rsquare;

end