function [isStable] = checkStability(y)
isStable = false;
len = length(y);
x = 1:len;

[slope,~,r2] = getLinReg(x,y);
avg = mean(y,'all');

if slope < avg * 0.01 || r2 > 0.9
    isStable = true;
end

end