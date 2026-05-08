function Struct = renameField(Struct,oldField,newField)

fields = fieldnames(Struct);
if any(strcmp(fields,oldField))
    [Struct.(newField)] = Struct.(oldField);
    Struct = rmfield(Struct,oldField);
end

end