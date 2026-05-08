function tf = checkTolerance(num1,num2,toleranceFraction)

fract = num1 * toleranceFraction;
num1_min = num1 - fract;
num1_max = num1 + fract;

tf = num2 >= num1_min && num2 <= num1_max;

end