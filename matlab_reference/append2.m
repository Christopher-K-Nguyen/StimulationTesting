function arr = append2(arr,val,isVertical)

if isVertical
    arr = [arr;val];
else
    arr = [arr,val];
end

end